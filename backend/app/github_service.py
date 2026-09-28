"""GitHub integration: commit/PR/deployment/release lookups + webhook verification.

Lookups never raise: on any GitHub failure they log and return None / [].
"""
import hashlib
import hmac
import logging
from typing import Any

import httpx

from .config import Settings, settings as default_settings
from .schemas import PullRequestInfo

logger = logging.getLogger("sre.github")
API = "https://api.github.com"


class GitHubService:
    def __init__(self, settings: Settings = default_settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        self._client = httpx.AsyncClient(base_url=API, headers=headers, timeout=10, transport=transport)

    @property
    def configured(self) -> bool:
        return bool(self.settings.github_repository)

    @property
    def webhook_secret_configured(self) -> bool:
        return bool(self.settings.github_webhook_secret)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params) -> Any | None:
        if not self.configured:
            return None
        try:
            resp = await self._client.get(path, params=params or None)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("GitHub lookup %s -> HTTP %s", path.split("?")[0], resp.status_code)
        except httpx.HTTPError as exc:
            logger.warning("GitHub unavailable: %s", type(exc).__name__)
        return None

    @property
    def _repo(self) -> str:
        return f"/repos/{self.settings.github_repository}"

    # ------------------------------------------------------------- lookups
    async def get_commit(self, sha: str) -> dict | None:
        data = await self._get(f"{self._repo}/commits/{sha}")
        if not data:
            return None
        commit = data.get("commit", {})
        return {
            "sha": data.get("sha"),
            "message": (commit.get("message") or "").split("\n")[0],
            "author": (commit.get("author") or {}).get("name"),
            "timestamp": (commit.get("author") or {}).get("date"),
            "url": data.get("html_url"),
            "changes": [f.get("filename") for f in (data.get("files") or [])[:15]],
            "stats": data.get("stats"),
        }

    async def get_pull_requests_for_commit(self, sha: str) -> list[PullRequestInfo]:
        data = await self._get(f"{self._repo}/commits/{sha}/pulls")
        return [PullRequestInfo(number=p["number"], title=p.get("title"), url=p.get("html_url")) for p in (data or [])]

    async def get_recent_commits(self, limit: int = 10) -> list[dict]:
        data = await self._get(f"{self._repo}/commits", per_page=limit) or []
        return [{"sha": c["sha"], "message": c["commit"]["message"].split("\n")[0],
                 "timestamp": c["commit"]["author"]["date"]} for c in data]

    async def get_recent_pull_requests(self, limit: int = 10) -> list[dict]:
        data = await self._get(f"{self._repo}/pulls", state="closed", sort="updated", direction="desc", per_page=limit) or []
        return [{"number": p["number"], "title": p["title"], "merged_at": p.get("merged_at"),
                 "merge_commit_sha": p.get("merge_commit_sha")} for p in data if p.get("merged_at")]

    async def get_recent_deployments(self, limit: int = 10) -> list[dict]:
        data = await self._get(f"{self._repo}/deployments", per_page=limit) or []
        return [{"id": d["id"], "sha": d["sha"], "environment": d.get("environment"), "created_at": d.get("created_at")} for d in data]

    async def get_latest_release(self) -> dict | None:
        d = await self._get(f"{self._repo}/releases/latest")
        return {"tag": d.get("tag_name"), "published_at": d.get("published_at"), "url": d.get("html_url")} if d else None

    # ------------------------------------------------------------- webhooks
    def verify_signature(self, body: bytes, signature_header: str | None) -> bool:
        secret = self.settings.github_webhook_secret
        if not secret or not signature_header:
            return False
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    @staticmethod
    def parse_webhook_event(event: str, p: dict) -> dict | None:
        """Normalize the events we care about; return None for everything else."""
        repo = (p.get("repository") or {}).get("full_name")
        if event == "push":
            head = p.get("head_commit") or {}
            if not head:
                return None
            return {"kind": "push", "repository": repo, "ref": p.get("ref"), "commit": head.get("id"),
                    "message": (head.get("message") or "").split("\n")[0], "timestamp": head.get("timestamp"),
                    "author": (head.get("author") or {}).get("name")}
        if event == "pull_request":
            pr = p.get("pull_request") or {}
            if p.get("action") != "closed" or not pr.get("merged"):
                return None
            return {"kind": "pull_request", "repository": repo, "number": pr.get("number"), "title": pr.get("title"),
                    "commit": pr.get("merge_commit_sha"), "timestamp": pr.get("merged_at"), "url": pr.get("html_url")}
        if event == "deployment":
            d = p.get("deployment") or {}
            return {"kind": "deployment", "repository": repo, "deployment_id": d.get("id"), "commit": d.get("sha"),
                    "environment": d.get("environment"), "ref": d.get("ref"), "timestamp": d.get("created_at")}
        if event == "deployment_status":
            d, s = p.get("deployment") or {}, p.get("deployment_status") or {}
            return {"kind": "deployment_status", "repository": repo, "deployment_id": d.get("id"),
                    "commit": d.get("sha"), "environment": d.get("environment"), "state": s.get("state"),
                    "timestamp": s.get("created_at")}
        if event == "release":
            r = p.get("release") or {}
            if p.get("action") != "published":
                return None
            return {"kind": "release", "repository": repo, "tag": r.get("tag_name"),
                    "commit": r.get("target_commitish"), "timestamp": r.get("published_at"), "url": r.get("html_url")}
        return None
