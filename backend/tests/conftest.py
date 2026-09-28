import json
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.alert_parser import tokenize
from app.config import Settings
from app.main import build_container, create_app

ROOT = Path(__file__).resolve().parent.parent

PROJECTOR = {
    "title": "Classroom Projector Has No Signal", "service": "Classroom Infrastructure", "error": "No Signal",
    "symptoms": "Projector is powered but laptop display is not appearing",
    "impact": "Faculty cannot conduct the presentation", "environment": "production", "severity": "high",
}
API_500 = {
    "title": "Production API Returning 500 Errors", "service": "Authentication API",
    "error": "500 Internal Server Error", "symptoms": "Login requests fail with HTTP 500 after a release",
    "impact": "Users cannot sign in", "environment": "production", "severity": "critical",
}
LLM_DEFAULT = {
    "summary": "Test summary", "root_cause": "Hypothesis: test cause", "recommended_actions": ["Check logs"],
    "prevention_steps": ["Add monitoring"], "confidence": 0.8,
    "why_recommended": "Historical evidence: none. Current inference: test.", "relevant_incident_ids": [],
}


class FakeHindsight:
    """In-memory stand-in for the Hindsight REST API (served through httpx.MockTransport)."""

    def __init__(self, down: bool = False):
        self.items: list[dict] = []
        self.down = down

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("hindsight down", request=request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        if path.endswith("/profile"):
            return httpx.Response(200, json={})
        if path.endswith("/memories/recall"):
            q = set(tokenize(json.loads(request.content)["query"]))
            scored = [(len(q & set(tokenize(i["content"]))), i) for i in self.items]
            res = [{"id": f"m{n}", "text": i["content"], "type": "world", "context": i.get("context"),
                    "metadata": i.get("metadata", {}), "document_id": i["document_id"]}
                   for n, (score, i) in enumerate(sorted(scored, key=lambda x: -x[0])) if score > 0]
            return httpx.Response(200, json={"results": res})
        if path.endswith("/memories"):
            for it in json.loads(request.content)["items"]:
                self.items = [x for x in self.items if x["document_id"] != it["document_id"]] + [it]
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404)


class FakeLLM:
    configured = True

    def __init__(self, response: dict | None = None, error: Exception | None = None):
        self.response, self.error, self.prompts = response, error, []

    async def generate_response(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return json.dumps(self.response or LLM_DEFAULT)


@pytest.fixture
def make_client(tmp_path):
    opened = []

    def _make(*, llm=None, hindsight=None, seed=False, secret="s3cret"):
        if seed:
            shutil.copy(ROOT / "data" / "seed_incidents.json", tmp_path / "seed_incidents.json")
        settings = Settings(
            _env_file=None, data_dir=str(tmp_path), hindsight_api_url="http://hindsight.test", hindsight_api_key="k",
            github_webhook_secret=secret, mock_deployments=True, seed_demo_data=seed, app_env="test", groq_api_key="x",
        )
        hs = hindsight or FakeHindsight()
        container = build_container(settings, llm=llm or FakeLLM(), memory_transport=httpx.MockTransport(hs.handler))
        client = TestClient(create_app(container))
        client.__enter__()
        opened.append(client)
        client.hs, client.container = hs, container
        return client

    yield _make
    for c in opened:
        c.__exit__(None, None, None)
