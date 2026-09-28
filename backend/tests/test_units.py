import asyncio
import types
from datetime import timedelta

import groq
import httpx
import pytest

from app.alert_parser import parse_incident
from app.config import Settings
from app.deployment_service import DeploymentProvider, DeploymentProviderError, DeploymentService
from app.github_service import GitHubService
from app.incident_agent import SREIncidentAgent
from app.llm_client import LLMClient, LLMError
from app.memory_manager import MemoryManager
from app.models import IncidentRecord, Repository, utcnow
from app.schemas import FailedAttemptItem, IncidentInput

from .conftest import API_500, PROJECTOR


def _req():
    return httpx.Request("POST", "https://api.groq.com/x")


def _resp(code):
    return httpx.Response(code, request=_req())


def _fake_groq(exc=None, content="hello"):
    async def create(**kwargs):
        if exc:
            raise exc
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


# ---------------------------------------------------------------- alert parser
def test_parser_distinguishes_hardware_from_software_incidents():
    hw = parse_incident(IncidentInput(**PROJECTOR))
    sw = parse_incident(IncidentInput(**API_500))
    assert hw.is_software is False and hw.infrastructure_component == "Display / AV equipment"
    assert sw.is_software is True and "500" in sw.error_codes and "http:500" in sw.tags


# ---------------------------------------------------------------- LLM client
@pytest.mark.parametrize("exc,kind", [
    (groq.AuthenticationError("bad key", response=_resp(401), body=None), "authentication"),
    (groq.RateLimitError("slow down", response=_resp(429), body=None), "rate_limit"),
    (groq.NotFoundError("no model", response=_resp(404), body=None), "invalid_model"),
    (groq.APITimeoutError(request=_req()), "timeout"),
    (groq.APIConnectionError(request=_req()), "connection"),
    (groq.InternalServerError("boom", response=_resp(500), body=None), "api_error"),
])
def test_llm_errors_are_mapped_not_raised_raw(exc, kind):
    client = LLMClient(Settings(_env_file=None, groq_api_key="x"), client=_fake_groq(exc))
    with pytest.raises(LLMError) as e:
        asyncio.run(client.generate_response("hi"))
    assert e.value.kind == kind


def test_llm_empty_missing_key_and_success():
    s = Settings(_env_file=None, groq_api_key="x")
    with pytest.raises(LLMError) as e:
        asyncio.run(LLMClient(s, client=_fake_groq(content="   ")).generate_response("hi"))
    assert e.value.kind == "empty_response"
    with pytest.raises(LLMError) as e:
        asyncio.run(LLMClient(Settings(_env_file=None, groq_api_key="")).generate_response("hi"))
    assert e.value.kind == "not_configured"
    assert asyncio.run(LLMClient(s, client=_fake_groq(content="ok")).generate_response("hi")) == "ok"


# ---------------------------------------------------------------- deployments
def test_no_correlation_outside_time_window():
    svc = DeploymentService(Settings(_env_file=None, mock_deployments=True))
    r = asyncio.run(svc.correlate(incident_time=utcnow() + timedelta(days=10),
                                  service="Authentication API", environment="production"))
    assert r.checked and not r.detected


def test_correlation_survives_provider_failure():
    class Boom(DeploymentProvider):
        name = "boom"

        async def get_recent_deployments(self, limit=10):
            raise DeploymentProviderError("provider down")

        async def get_deployment_details(self, deployment_id):
            raise DeploymentProviderError("provider down")

        async def rollback_deployment(self, deployment_id, reason):
            raise DeploymentProviderError("provider down")

    svc = DeploymentService(Settings(_env_file=None), provider=Boom())
    r = asyncio.run(svc.correlate(incident_time=utcnow(), service="x", environment="production"))
    assert r.checked is False and r.detected is False and "provider down" in r.error


# ---------------------------------------------------------------- github signature
def test_signature_verification():
    gh = GitHubService(Settings(_env_file=None, github_webhook_secret="abc"))
    import hashlib, hmac
    good = "sha256=" + hmac.new(b"abc", b"body", hashlib.sha256).hexdigest()
    assert gh.verify_signature(b"body", good)
    assert not gh.verify_signature(b"tampered", good)
    assert not gh.verify_signature(b"body", None)
    assert not GitHubService(Settings(_env_file=None)).verify_signature(b"body", good)  # no secret => reject


# ---------------------------------------------------------------- repository / agent helpers
def test_repository_persists_and_reserves_unique_ids(tmp_path):
    repo = Repository(str(tmp_path))
    assert repo.reserve_incident_id() == "INC-001"
    assert repo.reserve_incident_id() == "INC-002"
    repo.save_incident(IncidentRecord(incident_id="INC-001", title="t", service="s", error="e", symptoms="x"))
    assert Repository(str(tmp_path)).get_incident("INC-001").title == "t"


def test_repeated_failed_actions_are_dropped(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path))
    agent = SREIncidentAgent(memory=MemoryManager(s), llm=LLMClient(s))
    failed = [FailedAttemptItem(action="Restarted projector"), FailedAttemptItem(action="Cleared cache")]
    warnings: list[str] = []
    kept = agent._drop_repeated_failures(
        ["Restart the projector", "Clear the cache", "Replace HDMI cable", "Restart projector (new evidence: firmware crash in logs)"],
        failed, warnings)
    assert kept == ["Replace HDMI cable", "Restart projector (new evidence: firmware crash in logs)"]
    assert len(warnings) == 2
