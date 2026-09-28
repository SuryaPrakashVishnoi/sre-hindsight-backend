"""Hindsight memory layer.

Talks to Hindsight's REST API with httpx:
  GET  /health
  GET  /v1/default/banks/{bank}/profile
  POST /v1/default/banks/{bank}/memories          (retain)
  POST /v1/default/banks/{bank}/memories/recall   (recall)

Nothing here ever raises to callers because Hindsight is down:
store_* return False, search_memories returns available=False
with the standard notice.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field

import httpx

from .alert_parser import overlap_score, tokenize
from .config import Settings, settings as default_settings
from .models import (
    FeedbackRecord,
    IncidentRecord,
    Repository,
    RollbackRecord,
    ensure_utc,
)
from .schemas import MemoryHit


logger = logging.getLogger("sre.memory")

HISTORY_UNAVAILABLE_NOTICE = (
    "Historical memory is temporarily unavailable. "
    "This recommendation is based only on the current incident."
)

NO_HISTORY_NOTICE = (
    "No relevant historical incident was found in Hindsight memory."
)

_INC_RE = re.compile(r"\bINC-\d+\b")


class MemoryUnavailableError(Exception):
    pass


@dataclass
class MemorySearchResult:
    available: bool
    hits: list[MemoryHit] = field(default_factory=list)
    notice: str | None = None


def _h(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_incident_document(r: IncidentRecord) -> str:
    """Readable, fact-dense text so Hindsight extracts useful engineering experience."""

    L = [
        f"Incident {r.incident_id}: {r.title}",
        (
            f"Incident {r.incident_id} status: {r.status}. "
            f"Severity: {r.severity}. Environment: {r.environment}."
        ),
        (
            f"Incident {r.incident_id} service: {r.service}. "
            f"Infrastructure component: "
            f"{r.infrastructure_component or 'unknown'}."
        ),
        (
            f"Incident {r.incident_id} occurred at "
            f"{ensure_utc(r.timestamp).isoformat()}."
        ),
        f"Incident {r.incident_id} error: {r.error}",
        f"Incident {r.incident_id} symptoms: {r.symptoms}",
        f"Incident {r.incident_id} impact: {r.impact}",
    ]

    if r.root_cause:
        L.append(
            f"Incident {r.incident_id} verified root cause "
            f"(confirmed by engineer): {r.root_cause}"
        )
    else:
        L.append(
            f"Incident {r.incident_id} root cause: not yet verified."
        )

        hyp = (r.ai_analysis or {}).get("root_cause")

        if hyp:
            L.append(
                f"Incident {r.incident_id} unverified AI hypothesis "
                f"(NOT confirmed): {hyp}"
            )

    if r.resolution:
        L.append(
            f"Incident {r.incident_id} resolution: {r.resolution}"
        )

    for a in r.successful_actions:
        L.append(
            f"Incident {r.incident_id} successful action: "
            f"{a.action}. {a.notes}".strip()
        )

    for a in r.failed_actions:
        L.append(
            f"Incident {r.incident_id} failed troubleshooting attempt "
            f"(did not resolve the issue): {a.action}. {a.notes}".strip()
        )

    if r.deployment or r.commit or r.pull_request:
        L.append(
            f"Incident {r.incident_id} potential deployment correlation "
            f"(not proven causation): "
            f"version {r.deployment or 'unknown'}, "
            f"commit {r.commit or 'unknown'}, "
            f"PR {r.pull_request or 'unknown'}."
        )

    for f in r.engineer_feedback:
        L.append(
            f"Incident {r.incident_id} engineer feedback: "
            f"rating {f.rating}/5, useful={f.useful}, "
            f"action taken: {f.action_taken}, result: {f.result}."
        )

    if r.related_incidents:
        L.append(
            f"Incident {r.incident_id} related incidents: "
            f"{', '.join(r.related_incidents)}."
        )

    for line in r.logs[:5]:
        L.append(
            f"Incident {r.incident_id} log: {line[:200]}"
        )

    return "\n".join(L)


class MemoryManager:
    def __init__(
        self,
        settings: Settings = default_settings,
        repo: Repository | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.repo = (
            repo
            if repo is not None
            else Repository(settings.data_dir)
        )

        self.bank_id = settings.hindsight_bank_id

        # This starts False intentionally.
        # It becomes True after a successful Hindsight check/search.
        self.available = False

        self._client: httpx.AsyncClient | None = None

        # Diagnostic logging.
        # IMPORTANT: Never log the API key itself.
        logger.info(
            "HINDSIGHT DEBUG: initialized "
            "configured=%s api_url=%s bank_id=%s api_key_present=%s",
            self.configured,
            settings.hindsight_api_url,
            self.bank_id,
            bool(settings.hindsight_api_key),
        )

        if self.configured:
            headers = {
                "Content-Type": "application/json"
            }

            if settings.hindsight_api_key:
                headers["Authorization"] = (
                    f"Bearer {settings.hindsight_api_key}"
                )

            self._client = httpx.AsyncClient(
                base_url=settings.hindsight_api_url.rstrip("/"),
                headers=headers,
                transport=transport,
            )

            logger.info(
                "HINDSIGHT DEBUG: HTTP client created "
                "base_url=%s bank=%s",
                settings.hindsight_api_url.rstrip("/"),
                self.bank_id,
            )
        else:
            logger.warning(
                "HINDSIGHT DEBUG: Hindsight is not configured "
                "api_url_present=%s bank_id_present=%s",
                bool(settings.hindsight_api_url),
                bool(self.bank_id),
            )

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.hindsight_api_url
            and self.bank_id
        )

    @property
    def _bank_path(self) -> str:
        return f"/v1/default/banks/{self.bank_id}"

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()

    # ------------------------------------------------------------------ http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json=None,
        timeout: float | None = None,
    ) -> httpx.Response:

        if not self._client:
            logger.error(
                "HINDSIGHT DEBUG: request attempted without HTTP client"
            )
            raise MemoryUnavailableError(
                "Hindsight is not configured"
            )

        logger.info(
            "HINDSIGHT DEBUG: HTTP %s %s",
            method,
            path,
        )

        try:
            resp = await self._client.request(
                method,
                path,
                json=json,
                timeout=(
                    timeout
                    or self.settings.hindsight_timeout_seconds
                ),
            )

        except httpx.TimeoutException as exc:
            logger.exception(
                "HINDSIGHT DEBUG: request timed out "
                "method=%s path=%s",
                method,
                path,
            )
            raise MemoryUnavailableError(
                "Hindsight request timed out"
            ) from exc

        except httpx.HTTPError as exc:
            logger.exception(
                "HINDSIGHT DEBUG: HTTP connection error "
                "method=%s path=%s error=%s",
                method,
                path,
                type(exc).__name__,
            )
            raise MemoryUnavailableError(
                f"Hindsight connection error ({type(exc).__name__})"
            ) from exc

        logger.info(
            "HINDSIGHT DEBUG: HTTP %s %s -> %s",
            method,
            path,
            resp.status_code,
        )

        if resp.status_code in (401, 403):
            logger.error(
                "HINDSIGHT DEBUG: authentication failed "
                "HTTP %s for %s",
                resp.status_code,
                path,
            )

            raise MemoryUnavailableError(
                "Hindsight authentication failed"
            )

        if resp.status_code == 429:
            logger.error(
                "HINDSIGHT DEBUG: rate limit reached"
            )

            raise MemoryUnavailableError(
                "Hindsight rate limit reached"
            )

        if resp.status_code >= 400 and resp.status_code != 404:
            logger.error(
                "HINDSIGHT DEBUG: server returned HTTP %s "
                "for %s",
                resp.status_code,
                path,
            )

            raise MemoryUnavailableError(
                f"Hindsight returned HTTP {resp.status_code}"
            )

        return resp

    # --------------------------------------------------------------- lifecycle

    async def connect(self) -> bool:
        """Verify Hindsight is reachable and the memory bank is usable."""

        logger.info(
            "HINDSIGHT DEBUG: starting connection check "
            "url=%s bank=%s",
            self.settings.hindsight_api_url,
            self.bank_id,
        )

        try:
            logger.info(
                "HINDSIGHT DEBUG: calling GET /health"
            )

            resp = await self._request(
                "GET",
                "/health",
            )

            logger.info(
                "HINDSIGHT DEBUG: /health returned HTTP %s",
                resp.status_code,
            )

        except MemoryUnavailableError as exc:
            self.available = False

            logger.exception(
                "HINDSIGHT DEBUG: /health connection failed: %s",
                exc,
            )

            return False

        logger.info(
            "HINDSIGHT DEBUG: checking bank profile %s/profile",
            self._bank_path,
        )

        status = await self.check_memory_bank()

        logger.info(
            "HINDSIGHT DEBUG: bank check result: %s",
            status,
        )

        self.available = status["available"]

        logger.info(
            "HINDSIGHT DEBUG: FINAL connection result "
            "available=%s bank=%s",
            self.available,
            self.bank_id,
        )

        return self.available

    async def check_memory_bank(self) -> dict:
        try:
            resp = await self._request(
                "GET",
                f"{self._bank_path}/profile",
            )

            logger.info(
                "HINDSIGHT DEBUG: bank profile returned HTTP %s",
                resp.status_code,
            )

        except MemoryUnavailableError as exc:
            logger.exception(
                "HINDSIGHT DEBUG: bank profile check failed: %s",
                exc,
            )

            return {
                "available": False,
                "bank_id": self.bank_id,
                "exists": False,
                "notice": str(exc),
            }

        # 404 => bank is created automatically on first retain
        result = {
            "available": True,
            "bank_id": self.bank_id,
            "exists": resp.status_code != 404,
            "notice": None,
        }

        logger.info(
            "HINDSIGHT DEBUG: bank status=%s",
            result,
        )

        return result

    # ------------------------------------------------------------------ write

    async def _retain(
        self,
        content: str,
        *,
        document_id: str,
        context: str,
        tags: list[str],
        metadata: dict[str, str],
        timestamp: str | None = None,
    ) -> bool:

        item = {
            "content": content,
            "context": context,
            "document_id": document_id,
            "tags": tags,
            "metadata": metadata,
        }

        if timestamp:
            item["timestamp"] = timestamp

        try:
            resp = await self._request(
                "POST",
                f"{self._bank_path}/memories",
                json={"items": [item]},
                timeout=60,
            )

            logger.info(
                "HINDSIGHT DEBUG: retain succeeded "
                "document_id=%s HTTP=%s",
                document_id,
                resp.status_code,
            )

            return True

        except MemoryUnavailableError as exc:
            logger.exception(
                "HINDSIGHT DEBUG: retain failed "
                "document_id=%s error=%s",
                document_id,
                exc,
            )

            return False

    @staticmethod
    def _base_tags(
        r: IncidentRecord,
        kind: str,
    ) -> list[str]:

        return [
            "sre",
            f"kind:{kind}",
            f"incident:{r.incident_id}",
            f"service:{_slug(r.service)}",
            f"env:{r.environment}",
        ]

    @staticmethod
    def _meta(
        r: IncidentRecord,
        kind: str,
    ) -> dict[str, str]:

        return {
            "incident_id": r.incident_id,
            "kind": kind,
            "service": r.service,
            "environment": r.environment,
            "severity": r.severity,
            "status": r.status,
        }

    async def store_incident(
        self,
        record: IncidentRecord,
    ) -> bool:

        """Upsert the full incident document (document_id = incident id)."""

        ok = await self._retain(
            build_incident_document(record),
            document_id=record.incident_id,
            context="SRE incident record with engineering experience",
            tags=(
                self._base_tags(record, "incident")
                + record.tags
            ),
            metadata=self._meta(record, "incident"),
            timestamp=ensure_utc(record.timestamp).isoformat(),
        )

        record.memory_synced = ok
        self.repo.save_incident(record)

        logger.info(
            "Incident %s stored in Hindsight: %s",
            record.incident_id,
            ok,
        )

        return ok

    async def store_feedback(
        self,
        record: IncidentRecord,
        fb: FeedbackRecord,
    ) -> bool:

        text = (
            f"Engineer feedback for incident "
            f"{record.incident_id} "
            f"({record.title}, service {record.service}): "
            f"the recommendation was "
            f"{'useful' if fb.useful else 'not useful'} "
            f"(rating {fb.rating}/5). "
            f"Recommendation given: "
            f"{fb.recommendation or 'n/a'}. "
            f"Action taken: {fb.action_taken}. "
            f"Result: {fb.result}. "
            f"Actual root cause: "
            f"{fb.actual_root_cause or 'not stated'}."
        )

        ok = await self._retain(
            text,
            document_id=(
                f"{record.incident_id}:feedback:"
                f"{int(fb.recorded_at.timestamp())}"
            ),
            context="Engineer feedback on an incident recommendation",
            tags=self._base_tags(record, "feedback"),
            metadata=self._meta(record, "feedback"),
        )

        logger.info(
            "Feedback for %s stored: %s",
            record.incident_id,
            ok,
        )

        return ok

    async def store_successful_experience(
        self,
        record: IncidentRecord,
        action: str,
        notes: str = "",
    ) -> bool:

        text = (
            f"Successful fix for incident "
            f"{record.incident_id} "
            f"({record.title}, service {record.service}, "
            f"error {record.error}): "
            f"{action}. {notes} "
            f"Root cause: "
            f"{record.root_cause or 'not stated'}."
        )

        return await self._retain(
            text,
            document_id=(
                f"{record.incident_id}:success:{_h(action)}"
            ),
            context="Successful troubleshooting action",
            tags=self._base_tags(
                record,
                "successful_action",
            ),
            metadata=self._meta(
                record,
                "successful_action",
            ),
        )

    async def store_failed_attempt(
        self,
        record: IncidentRecord,
        action: str,
        notes: str = "",
    ) -> bool:

        text = (
            f"Failed troubleshooting attempt for incident "
            f"{record.incident_id} "
            f"({record.title}, service {record.service}, "
            f"error {record.error}): "
            f"{action}. "
            f"This did not resolve the issue. {notes}"
        )

        return await self._retain(
            text,
            document_id=(
                f"{record.incident_id}:failed:{_h(action)}"
            ),
            context=(
                "Failed troubleshooting attempt "
                "(do not repeat without new evidence)"
            ),
            tags=self._base_tags(
                record,
                "failed_attempt",
            ),
            metadata=self._meta(
                record,
                "failed_attempt",
            ),
        )

    async def store_rollback_event(
        self,
        rb: RollbackRecord,
        record: IncidentRecord | None = None,
    ) -> bool:

        inc = rb.incident_id or "unlinked"

        text = (
            f"Rollback event {rb.rollback_id} "
            f"for incident {inc}: deployment "
            f"{rb.version or rb.deployment_id} "
            f"of service {rb.service or 'unknown'} "
            f"({rb.environment or 'unknown'}) was rolled back. "
            f"Rollback status: {rb.status}. "
            f"Result: {rb.result}. "
            f"Reason: {rb.reason}. "
            f"Whether the incident itself was resolved "
            f"must be confirmed by engineer feedback."
        )

        tags = [
            "sre",
            "kind:rollback",
            f"deployment:{rb.deployment_id}",
        ]

        meta = {
            "kind": "rollback",
            "rollback_id": rb.rollback_id,
            "deployment_id": rb.deployment_id,
            "status": rb.status,
        }

        if rb.incident_id:
            tags.append(
                f"incident:{rb.incident_id}"
            )
            meta["incident_id"] = rb.incident_id

        ok = await self._retain(
            text,
            document_id=f"rollback:{rb.rollback_id}",
            context="Deployment rollback outcome",
            tags=tags,
            metadata=meta,
            timestamp=ensure_utc(
                rb.requested_at
            ).isoformat(),
        )

        logger.info(
            "Rollback %s stored in Hindsight: %s",
            rb.rollback_id,
            ok,
        )

        return ok

    async def store_deployment_event(
        self,
        summary: dict,
    ) -> bool:

        text = "GitHub {kind} event in {repo}: {desc}".format(
            kind=summary.get("kind"),
            repo=summary.get(
                "repository",
                "repository",
            ),
            desc="; ".join(
                f"{k}={v}"
                for k, v in summary.items()
                if k not in {
                    "kind",
                    "repository",
                }
                and v
            ),
        )

        return await self._retain(
            text,
            document_id=(
                f"github:{summary.get('kind')}:"
                f"{summary.get('commit') or summary.get('tag') or summary.get('deployment_id') or summary.get('number')}"
            ),
            context="Deployment / change history from GitHub",
            tags=[
                "sre",
                "kind:deployment",
                f"github:{summary.get('kind')}",
            ],
            metadata={
                "kind": "deployment",
                "commit": str(
                    summary.get("commit") or ""
                ),
            },
            timestamp=summary.get("timestamp"),
        )

    async def sync_pending(self) -> int:
        """Push records that never reached Hindsight (e.g. it was down)."""

        done = 0

        for rec in self.repo.unsynced_incidents():
            if not await self.store_incident(rec):
                break

            done += 1

        if done:
            logger.info(
                "Synced %d pending incident(s) to Hindsight",
                done,
            )

        return done

    # ------------------------------------------------------------------- read

    async def search_memories(
        self,
        query: str,
        limit: int = 8,
    ) -> MemorySearchResult:

        logger.info(
            "HINDSIGHT DEBUG: starting memory recall "
            "query=%s limit=%d",
            query,
            limit,
        )

        try:
            resp = await self._request(
                "POST",
                f"{self._bank_path}/memories/recall",
                json={
                    "query": query,
                    "max_tokens": 2048,
                    "budget": "mid",
                },
            )

            logger.info(
                "HINDSIGHT DEBUG: recall returned HTTP %s",
                resp.status_code,
            )

            if resp.status_code == 404:
                logger.info(
                    "HINDSIGHT DEBUG: bank does not exist yet; "
                    "returning empty history"
                )

                self.available = True

                return MemorySearchResult(
                    True,
                    [],
                    NO_HISTORY_NOTICE,
                )

            data = resp.json()

        except (MemoryUnavailableError, ValueError) as exc:
            logger.exception(
                "HINDSIGHT DEBUG: memory search failed: %s",
                exc,
            )

            self.available = False

            return MemorySearchResult(
                False,
                [],
                HISTORY_UNAVAILABLE_NOTICE,
            )

        q_tokens = tokenize(query)

        raw = (
            data.get("results")
            if isinstance(data, dict)
            else data
        )

        raw = raw or []

        hits: list[MemoryHit] = []

        logger.info(
            "HINDSIGHT DEBUG: Hindsight returned %d raw result(s)",
            len(raw),
        )

        for rank, item in enumerate(raw):

            if (
                not isinstance(item, dict)
                or not item.get("text")
            ):
                continue

            meta = (
                item.get("metadata")
                if isinstance(
                    item.get("metadata"),
                    dict,
                )
                else {}
            )

            haystack = " ".join(
                [
                    str(
                        item.get(
                            "document_id"
                        )
                        or ""
                    ),
                    str(
                        item.get(
                            "context"
                        )
                        or ""
                    ),
                    item["text"],
                ]
            )

            match = _INC_RE.search(haystack)

            m = (
                meta.get("incident_id")
                or (
                    match.group(0)
                    if match
                    else None
                )
            )

            # Hindsight returns ranked results without a portable score,
            # so relevance is a transparent heuristic:
            # 15% rank position + 85% keyword overlap.
            rank_score = (
                1.0
                - (
                    rank
                    / max(len(raw), 1)
                )
                * 0.6
            )

            relevance = round(
                min(
                    1.0,
                    0.15 * rank_score
                    + 0.85
                    * overlap_score(
                        q_tokens,
                        item["text"],
                    ),
                ),
                2,
            )

            hits.append(
                MemoryHit(
                    memory_id=(
                        str(item.get("id"))
                        if item.get("id")
                        else None
                    ),
                    incident_id=m,
                    kind=(
                        meta.get("kind")
                        or item.get("type")
                    ),
                    text=item["text"],
                    relevance=relevance,
                    metadata=meta,
                )
            )

        hits = sorted(
            hits,
            key=lambda h: h.relevance,
            reverse=True,
        )[:limit]

        logger.info(
            "HINDSIGHT DEBUG: memory search succeeded "
            "hits=%d; setting available=True",
            len(hits),
        )

        self.available = True

        return MemorySearchResult(
            True,
            hits,
            None if hits else NO_HISTORY_NOTICE,
        )

    async def get_memory(
        self,
        incident_id: str,
    ) -> dict | None:

        """Structured record + related Hindsight memories."""

        record = self.repo.get_incident(
            incident_id
        )

        result = await self.search_memories(
            f"Incident {incident_id}",
            limit=10,
        )

        related = [
            h
            for h in result.hits
            if h.incident_id == incident_id
        ]

        if record is None and not related:
            return None

        return {
            "incident": (
                record.model_dump(
                    mode="json"
                )
                if record
                else None
            ),
            "related_memories": [
                h.model_dump(
                    mode="json"
                )
                for h in related
            ],
            "memory_available": result.available,
            "notice": (
                result.notice
                if not result.available
                else None
            ),
        }