"""Deployment abstraction: Mock / Render / Vercel providers + correlation + rollback."""
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from uuid import uuid4

import httpx

from .alert_parser import overlap_score, tokenize
from .config import Settings, settings as default_settings
from .github_service import GitHubService
from .models import RollbackRecord, ensure_utc, utcnow
from .schemas import Deployment, DeploymentCorrelation, PullRequestInfo

logger = logging.getLogger("sre.deploy")


class DeploymentProviderError(Exception):
    pass


class DeploymentNotFoundError(Exception):
    pass


class DeploymentProvider(ABC):
    name = "base"
    mock = False

    @abstractmethod
    async def get_recent_deployments(self, limit: int = 10) -> list[Deployment]: ...

    @abstractmethod
    async def get_deployment_details(self, deployment_id: str) -> Deployment | None: ...

    @abstractmethod
    async def rollback_deployment(self, deployment_id: str, reason: str) -> dict:
        """Return {"status": "success"|"failed", "message": str}."""


async def _call(client: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    try:
        resp = await client.request(method, url, **kw)
    except httpx.TimeoutException as exc:
        raise DeploymentProviderError("Deployment provider timed out") from exc
    except httpx.HTTPError as exc:
        raise DeploymentProviderError(f"Deployment provider unreachable ({type(exc).__name__})") from exc
    if resp.status_code in (401, 403):
        raise DeploymentProviderError("Deployment provider authentication failed")
    if resp.status_code == 429:
        raise DeploymentProviderError("Deployment provider rate limit reached")
    if resp.status_code == 404:
        return resp
    if resp.status_code >= 400:
        raise DeploymentProviderError(f"Deployment provider returned HTTP {resp.status_code}")
    return resp


def _parse_ts(value) -> datetime:
    if isinstance(value, (int, float)):  # epoch millis (Vercel)
        return datetime.fromtimestamp(value / 1000, tz=utcnow().tzinfo)
    return ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


# ------------------------------------------------------------------ mock
class MockProvider(DeploymentProvider):
    """Realistic in-memory deployments. Timestamps are anchored to server start so a
    freshly created incident lands shortly after the latest deploy (great for demos)."""

    name = "mock"
    mock = True

    def __init__(self):
        now = utcnow()
        def d(i, ver, mins_ago, sha, pr, msg, status):
            return Deployment(deployment_id=i, version=ver, service="Authentication API", environment="production",
                              commit=sha, pull_request=f"#{pr}", timestamp=now - timedelta(minutes=mins_ago),
                              status=status, commit_message=msg, provider="mock", mock=True)
        self._deps = {x.deployment_id: x for x in [
            d("dep-1084", "v1.8.4", 5, "8f72abc", 142, "Refactor authentication middleware", "live"),
            d("dep-1083", "v1.8.3", 26 * 60, "3c19d0e", 139, "Add rate limiting to login endpoint", "superseded"),
            d("dep-1082", "v1.8.2", 3 * 24 * 60, "a41be77", 136, "Bump session library", "superseded"),
        ]}

    async def get_recent_deployments(self, limit: int = 10) -> list[Deployment]:
        return sorted(self._deps.values(), key=lambda x: x.timestamp, reverse=True)[:limit]

    async def get_deployment_details(self, deployment_id: str) -> Deployment | None:
        return self._deps.get(deployment_id)

    async def rollback_deployment(self, deployment_id: str, reason: str) -> dict:
        dep = self._deps.get(deployment_id)
        if not dep:
            raise DeploymentProviderError("Deployment not found")
        if dep.status == "rolled_back":
            return {"status": "failed", "message": f"[MOCK] {dep.version} was already rolled back."}
        ordered = sorted(self._deps.values(), key=lambda x: x.timestamp, reverse=True)
        previous = next((x for x in ordered if x.timestamp < dep.timestamp), None)
        dep.status = "rolled_back"
        if previous:
            previous.status = "live"
        target = previous.version if previous else "previous release"
        return {"status": "success", "message": f"[MOCK] Simulated rollback of {dep.version} to {target}. No real infrastructure was touched."}


# ---------------------------------------------------------------- render
class RenderProvider(DeploymentProvider):
    name = "render"
    BASE = "https://api.render.com/v1"

    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not (s.render_api_key and s.render_service_id):
            raise DeploymentProviderError("Render is not configured (RENDER_API_KEY / RENDER_SERVICE_ID)")
        self.s = s
        self._c = httpx.AsyncClient(base_url=self.BASE, timeout=15, transport=transport,
                                    headers={"Authorization": f"Bearer {s.render_api_key}", "Accept": "application/json"})

    def _map(self, raw: dict) -> Deployment:
        raw = raw.get("deploy", raw)
        commit = raw.get("commit") or {}
        sha = commit.get("id")
        return Deployment(
            deployment_id=raw["id"], version=(sha or raw["id"])[:7], service=self.s.deployment_service_name,
            environment="production", commit=sha[:7] if sha else None, timestamp=_parse_ts(raw.get("createdAt")),
            status=raw.get("status", "unknown"), commit_message=(commit.get("message") or "").split("\n")[0] or None,
            provider="render",
        )

    async def get_recent_deployments(self, limit: int = 10) -> list[Deployment]:
        r = await _call(self._c, "GET", f"/services/{self.s.render_service_id}/deploys", params={"limit": limit})
        return [self._map(x) for x in (r.json() or [])]

    async def get_deployment_details(self, deployment_id: str) -> Deployment | None:
        r = await _call(self._c, "GET", f"/services/{self.s.render_service_id}/deploys/{deployment_id}")
        return None if r.status_code == 404 else self._map(r.json())

    async def rollback_deployment(self, deployment_id: str, reason: str) -> dict:
        r = await _call(self._c, "POST", f"/services/{self.s.render_service_id}/rollback", json={"deployId": deployment_id})
        if r.status_code == 404:
            return {"status": "failed", "message": "Render could not find that deploy."}
        return {"status": "success", "message": "Render accepted the rollback request."}


# ---------------------------------------------------------------- vercel
class VercelProvider(DeploymentProvider):
    name = "vercel"
    BASE = "https://api.vercel.com"

    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not (s.vercel_token and s.vercel_project_id):
            raise DeploymentProviderError("Vercel is not configured (VERCEL_TOKEN / VERCEL_PROJECT_ID)")
        self.s = s
        self._c = httpx.AsyncClient(base_url=self.BASE, timeout=15, transport=transport,
                                    headers={"Authorization": f"Bearer {s.vercel_token}"})

    @property
    def _team(self) -> dict:
        return {"teamId": self.s.vercel_team_id} if self.s.vercel_team_id else {}

    def _map(self, raw: dict) -> Deployment:
        meta = raw.get("meta") or {}
        sha = meta.get("githubCommitSha")
        pr = meta.get("githubPrId")
        return Deployment(
            deployment_id=raw.get("uid") or raw.get("id"), version=(sha or raw.get("uid") or raw.get("id"))[:7],
            service=raw.get("name") or self.s.deployment_service_name,
            environment="production" if raw.get("target") == "production" else "preview",
            commit=sha[:7] if sha else None, pull_request=f"#{pr}" if pr else None,
            timestamp=_parse_ts(raw.get("created") or raw.get("createdAt")),
            status=(raw.get("state") or raw.get("readyState") or "unknown").lower(),
            commit_message=meta.get("githubCommitMessage"), provider="vercel",
        )

    async def get_recent_deployments(self, limit: int = 10) -> list[Deployment]:
        r = await _call(self._c, "GET", "/v6/deployments",
                        params={"projectId": self.s.vercel_project_id, "limit": limit, **self._team})
        return [self._map(x) for x in r.json().get("deployments", [])]

    async def get_deployment_details(self, deployment_id: str) -> Deployment | None:
        r = await _call(self._c, "GET", f"/v13/deployments/{deployment_id}", params=self._team)
        return None if r.status_code == 404 else self._map(r.json())

    async def rollback_deployment(self, deployment_id: str, reason: str) -> dict:
        r = await _call(self._c, "POST", f"/v9/projects/{self.s.vercel_project_id}/rollback/{deployment_id}", params=self._team)
        if r.status_code == 404:
            return {"status": "failed", "message": "Vercel could not find that deployment."}
        return {"status": "success", "message": "Vercel accepted the rollback request."}


class _Unconfigured(DeploymentProvider):
    name = "unconfigured"

    async def _fail(self, *a, **k):
        raise DeploymentProviderError("No deployment provider configured (set MOCK_DEPLOYMENTS=true or provider credentials)")

    get_recent_deployments = get_deployment_details = rollback_deployment = _fail


# --------------------------------------------------------------- service
class DeploymentService:
    def __init__(self, settings: Settings = default_settings, provider: DeploymentProvider | None = None,
                 github: GitHubService | None = None):
        self.settings = settings
        self.github = github
        self.provider = provider or self._select_provider(settings)

    @staticmethod
    def _select_provider(s: Settings) -> DeploymentProvider:
        if s.mock_deployments:
            return MockProvider()
        for cls in (RenderProvider, VercelProvider):
            try:
                return cls(s)
            except DeploymentProviderError:
                continue
        return _Unconfigured()

    @property
    def mock_mode(self) -> bool:
        return self.provider.mock

    async def recent(self, limit: int = 10) -> list[Deployment]:
        return await self.provider.get_recent_deployments(limit)

    async def details(self, deployment_id: str) -> Deployment:
        dep = await self.provider.get_deployment_details(deployment_id)
        if dep is None:
            raise DeploymentNotFoundError(deployment_id)
        return dep

    async def rollback(self, deployment_id: str, reason: str, incident_id: str | None = None) -> RollbackRecord:
        dep = await self.details(deployment_id)
        rb = RollbackRecord(rollback_id=f"RB-{uuid4().hex[:8].upper()}", deployment_id=deployment_id,
                            version=dep.version, service=dep.service, environment=dep.environment,
                            incident_id=incident_id, reason=reason, provider=self.provider.name, mock=self.mock_mode)
        logger.info("Rollback requested: %s deployment=%s mock=%s", rb.rollback_id, deployment_id, rb.mock)
        try:
            out = await self.provider.rollback_deployment(deployment_id, reason)
            rb.status, rb.result = out["status"], out["message"]
        except DeploymentProviderError as exc:
            rb.status, rb.result = "failed", str(exc)
        rb.completed_at = utcnow()
        logger.info("Rollback result: %s status=%s", rb.rollback_id, rb.status)
        return rb

    async def correlate(self, *, incident_time: datetime, service: str, environment: str) -> DeploymentCorrelation:
        incident_time = ensure_utc(incident_time)
        base = DeploymentCorrelation(checked=True, mock_mode=self.mock_mode, incident_time=incident_time,
                                     message="No deployment was found shortly before the incident.")
        try:
            deployments = await self.provider.get_recent_deployments(20)
        except DeploymentProviderError as exc:
            logger.warning("Deployment lookup failed: %s", exc)
            return base.model_copy(update={"checked": False, "error": str(exc),
                                           "message": "Deployment history could not be retrieved, so correlation was not evaluated."})

        window = timedelta(minutes=self.settings.correlation_window_minutes)
        cands = [d for d in deployments
                 if d.environment.lower() == environment.lower()
                 and timedelta(0) <= incident_time - ensure_utc(d.timestamp) <= window]
        svc_tokens = tokenize(service)
        same_service = [d for d in cands if overlap_score(svc_tokens, d.service) > 0]
        cands = same_service or cands
        if not cands:
            return base

        dep = max(cands, key=lambda d: ensure_utc(d.timestamp))
        minutes = round((incident_time - ensure_utc(dep.timestamp)).total_seconds() / 60, 1)
        strength = "close" if minutes <= 30 else "moderate" if minutes <= 120 else "distant"

        commit_msg, pr = dep.commit_message, None
        if dep.pull_request and dep.pull_request.lstrip("#").isdigit():
            pr = PullRequestInfo(number=int(dep.pull_request.lstrip("#")))
        if dep.commit and self.github and self.github.configured and not self.mock_mode:
            info = await self.github.get_commit(dep.commit)
            if info:
                commit_msg = info.get("message") or commit_msg
            prs = await self.github.get_pull_requests_for_commit(dep.commit)
            pr = prs[0] if prs else pr

        pr_txt = f", PR #{pr.number}" if pr else ""
        logger.info("Deployment correlation: %s %s minutes before incident (%s)", dep.version, minutes, strength)
        return base.model_copy(update={
            "detected": True, "strength": strength, "deployment": dep, "minutes_before_incident": minutes,
            "commit_sha": dep.commit, "commit_message": commit_msg, "pull_request": pr,
            "message": (f"Potential deployment correlation detected: {dep.version} (commit {dep.commit or 'unknown'}{pr_txt}) "
                        f"was deployed {minutes:g} minutes before the incident. Correlation is not proof of causation."),
        })
