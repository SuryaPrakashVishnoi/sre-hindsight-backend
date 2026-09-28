"""SRE Hindsight API. Run: uvicorn app.main:app --reload --port 8000"""
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings, settings as default_settings
from .deployment_service import (
    DeploymentNotFoundError, DeploymentProvider, DeploymentProviderError, DeploymentService,
)
from .github_service import GitHubService
from .incident_agent import IncidentNotFoundError, SREIncidentAgent
from .llm_client import LLMClient
from .memory_manager import MemoryManager
from .models import GitHubEventRecord, IncidentRecord, Repository
from .schemas import (
    AnalyzeResponse, FeedbackInput, FeedbackResponse, IncidentInput, RollbackRequest,
)

logger = logging.getLogger("sre.api")


class ApiError(Exception):
    def __init__(self, status: int, error: str, fallback: str | None = None):
        self.status, self.error, self.fallback = status, error, fallback


@dataclass
class Container:
    settings: Settings
    repo: Repository
    memory: MemoryManager
    llm: LLMClient
    github: GitHubService
    deployments: DeploymentService
    agent: SREIncidentAgent


def build_container(
    settings: Settings = default_settings, *, llm: LLMClient | None = None,
    memory_transport: httpx.AsyncBaseTransport | None = None,
    github_transport: httpx.AsyncBaseTransport | None = None,
    provider: DeploymentProvider | None = None,
) -> Container:
    repo = Repository(settings.data_dir)
    memory = MemoryManager(settings, repo, transport=memory_transport)
    llm = llm or LLMClient(settings)
    github = GitHubService(settings, transport=github_transport)
    deployments = DeploymentService(settings, provider=provider, github=github)
    agent = SREIncidentAgent(memory=memory, llm=llm, deployments=deployments, repo=repo)
    return Container(settings, repo, memory, llm, github, deployments, agent)


def seed_demo_data(repo: Repository) -> int:
    path = Path(repo.dir) / "seed_incidents.json"
    if not path.exists():
        return 0
    added = 0
    for raw in json.loads(path.read_text(encoding="utf-8")):
        if repo.get_incident(raw["incident_id"]) is None:
            repo.save_incident(IncidentRecord(**raw, source="seed"))
            added += 1
    return added


def _summary(r: IncidentRecord) -> dict:
    return {
        "incident_id": r.incident_id, "title": r.title, "service": r.service, "severity": r.severity,
        "environment": r.environment, "status": r.status, "timestamp": r.timestamp.isoformat(),
        "root_cause": r.root_cause, "confidence": r.confidence, "deployment": r.deployment,
        "source": r.source, "memory_synced": r.memory_synced,
    }


def create_app(container: Container | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # keeps URLs/headers out of logs

    c = container or build_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if c.settings.seed_demo_data and not c.settings.is_production:
            n = seed_demo_data(c.repo)
            if n:
                logger.info("Seeded %d demo incident(s)", n)
        if c.memory.configured:
            await c.memory.connect()
            await c.memory.sync_pending()
        else:
            logger.warning("Hindsight is not configured; running without historical memory")
        yield
        await c.memory.aclose()
        await c.github.aclose()

    app = FastAPI(title="SRE Hindsight", description="AI incident response agent with persistent organizational memory",
                  version="1.0.0", lifespan=lifespan)
    app.state.container = c
    app.add_middleware(CORSMiddleware, allow_origins=c.settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"])

    # ------------------------------------------------------------ errors
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse({"success": False, "error": exc.error, "fallback": exc.fallback}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(x) for x in e["loc"] if x != "body"), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"success": False, "error": "Invalid request", "details": details}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error: %s", type(exc).__name__)
        return JSONResponse({"success": False, "error": "Internal server error",
                             "fallback": "The request failed unexpectedly; external services were not the cause of a crash."}, status_code=500)

    # ------------------------------------------------------------ health
    @app.get("/api/health")
    async def health():
        return {"success": True, "status": "healthy", "service": "SRE Hindsight"}

    @app.get("/api/status")
    async def status():
        s = c.settings
        return {"success": True, "environment": s.app_env, "mock_deployments": s.mock_deployments,
                "hindsight_configured": c.memory.configured, "hindsight_available": c.memory.available,
                "llm_configured": c.llm.configured, "github_configured": c.github.configured,
                "webhook_secret_configured": c.github.webhook_secret_configured,
                "deployment_provider": c.deployments.provider.name, "incident_count": c.repo.count_incidents()}

    # ------------------------------------------------------------ analyze
    @app.post("/api/analyze", response_model=AnalyzeResponse)
    async def analyze(incident: IncidentInput):
        return await c.agent.analyze(incident)

    # ------------------------------------------------------------ feedback
    @app.post("/api/feedback", response_model=FeedbackResponse)
    async def feedback(fb: FeedbackInput):
        try:
            return await c.agent.learn_from_feedback(fb)
        except IncidentNotFoundError:
            raise ApiError(404, f"Incident {fb.incident_id} not found")

    # ------------------------------------------------------------ incidents
    @app.get("/api/incidents")
    async def incidents(status: str | None = None, service: str | None = None, limit: int = Query(50, ge=1, le=200)):
        rows = c.repo.list_incidents()
        if status:
            rows = [r for r in rows if r.status == status]
        if service:
            rows = [r for r in rows if service.lower() in r.service.lower()]
        rows = rows[:limit]
        return {"success": True, "count": len(rows), "incidents": [_summary(r) for r in rows]}

    @app.get("/api/incidents/{incident_id}")
    async def incident_detail(incident_id: str):
        rec = c.repo.get_incident(incident_id)
        if rec is None:
            raise ApiError(404, f"Incident {incident_id} not found")
        return {"success": True, "incident": rec.model_dump(mode="json")}

    # ------------------------------------------------------------ memory
    @app.get("/api/memory/search")
    async def memory_search(
        q: str | None = None, incident: str | None = None, error: str | None = None,
        service: str | None = None, root_cause: str | None = None, keyword: str | None = None,
        symptoms: str | None = None, environment: str | None = None, limit: int = Query(10, ge=1, le=25),
    ):
        query = " ".join(v for v in (q, incident, error, service, root_cause, keyword, symptoms) if v)
        if not query:
            raise ApiError(400, "Provide at least one search parameter (q, incident, error, service, root_cause, keyword, symptoms)")
        result = await c.memory.search_memories(query, limit=limit * 2)
        hits = result.hits
        for value in (service, environment):
            if value:
                hits = [h for h in hits if value.lower() in (h.text + " " + json.dumps(h.metadata)).lower()]
        hits = hits[:limit]
        return {"success": True, "query": query, "memory_available": result.available,
                "notice": result.notice if (not result.available or not hits) else None,
                "count": len(hits), "results": [h.model_dump(mode="json") for h in hits]}

    @app.get("/api/memory/{incident_id}")
    async def memory_detail(incident_id: str):
        data = await c.memory.get_memory(incident_id)
        if data is None:
            raise ApiError(404, f"No memory found for {incident_id}")
        return {"success": True, **data}

    # ------------------------------------------------------------ deployments
    @app.get("/api/deployments/recent")
    async def deployments_recent(limit: int = Query(10, ge=1, le=50)):
        try:
            deps = await c.deployments.recent(limit)
        except DeploymentProviderError as exc:
            raise ApiError(503, str(exc), "Incident analysis can continue without deployment data.")
        return {"success": True, "mock_mode": c.deployments.mock_mode, "provider": c.deployments.provider.name,
                "deployments": [d.model_dump(mode="json") for d in deps]}

    @app.get("/api/deployments/{deployment_id}")
    async def deployment_detail(deployment_id: str):
        try:
            dep = await c.deployments.details(deployment_id)
        except DeploymentNotFoundError:
            raise ApiError(404, f"Deployment {deployment_id} not found")
        except DeploymentProviderError as exc:
            raise ApiError(503, str(exc), "Incident analysis can continue without deployment data.")
        return {"success": True, "mock_mode": c.deployments.mock_mode, "deployment": dep.model_dump(mode="json")}

    @app.post("/api/deployments/{deployment_id}/rollback")
    async def rollback(deployment_id: str, body: RollbackRequest):
        # Safety: never roll back without explicit, matching confirmation.
        if not body.confirm:
            raise ApiError(400, "Rollback requires explicit confirmation (confirm=true).")
        if body.confirm_deployment_id != deployment_id:
            raise ApiError(400, "confirm_deployment_id must match the deployment being rolled back.")
        if body.incident_id and c.repo.get_incident(body.incident_id) is None:
            raise ApiError(404, f"Incident {body.incident_id} not found")
        try:
            rb = await c.deployments.rollback(deployment_id, body.reason, body.incident_id)
        except DeploymentNotFoundError:
            raise ApiError(404, f"Deployment {deployment_id} not found")
        except DeploymentProviderError as exc:
            raise ApiError(502, str(exc), "No changes were made.")
        stored = await c.agent.record_rollback(rb)
        ok = rb.status == "success"
        payload = {"success": ok, "rollback": rb.model_dump(mode="json"), "mock_mode": rb.mock, "memory_stored": stored,
                   "notice": "MOCK MODE: simulated rollback, no real infrastructure was touched." if rb.mock else None}
        return JSONResponse(payload, status_code=200 if ok else 502)

    # ------------------------------------------------------------ github
    @app.post("/api/github/webhook")
    async def github_webhook(request: Request):
        if not c.github.webhook_secret_configured:
            raise ApiError(503, "GitHub webhook secret is not configured", "Webhook ignored.")
        body = await request.body()
        if not c.github.verify_signature(body, request.headers.get("X-Hub-Signature-256")):
            logger.warning("Rejected GitHub webhook: invalid signature")
            raise ApiError(401, "Invalid webhook signature")
        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return {"success": True, "message": "pong"}
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise ApiError(400, "Webhook body is not valid JSON")
        summary = c.github.parse_webhook_event(event, payload)
        if summary is None:
            return {"success": True, "ignored": True, "event": event}
        c.repo.add_github_event(GitHubEventRecord(delivery_id=request.headers.get("X-GitHub-Delivery"), event_type=event, summary=summary))
        stored = False
        if summary["kind"] in {"deployment", "deployment_status", "release", "pull_request"}:
            stored = await c.memory.store_deployment_event(summary)
        logger.info("GitHub webhook accepted: event=%s", event)
        return {"success": True, "event": event, "recorded": True, "memory_stored": stored}

    return app


app = create_app()
