import hmac
import json
from hashlib import sha256

from app.llm_client import LLMError

from .conftest import API_500, LLM_DEFAULT, PROJECTOR, FakeHindsight, FakeLLM

NO_HISTORY = "No relevant historical incident was found in Hindsight memory."
HS_DOWN = ("Historical memory is temporarily unavailable. "
           "This recommendation is based only on the current incident.")


# ---------------------------------------------------------------- health / validation
def test_health(make_client):
    r = make_client().get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"success": True, "status": "healthy", "service": "SRE Hindsight"}


def test_incident_validation(make_client):
    client = make_client()
    r = client.post("/api/analyze", json={})
    assert r.status_code == 422 and r.json()["success"] is False
    r = client.post("/api/analyze", json={**PROJECTOR, "environment": "mars"})
    assert r.status_code == 422


# ---------------------------------------------------------------- analysis
def test_analyze_without_history_does_not_fabricate(make_client):
    client = make_client()
    r = client.post("/api/analyze", json=PROJECTOR)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["incident_id"] == "INC-001"
    a = body["analysis"]
    assert a["historical_matches"] == [] and a["memory_notice"] == NO_HISTORY
    assert 0 <= a["confidence"] <= 1
    stored = client.get(f"/api/incidents/{body['incident_id']}").json()["incident"]
    assert stored["root_cause"] is None  # an AI guess must never become "verified" memory
    assert any("INC-001" in i["content"] for i in client.hs.items)  # stored in Hindsight
    assert client.get("/api/incidents").json()["count"] == 1


def test_hindsight_down_analysis_continues(make_client):
    client = make_client(hindsight=FakeHindsight(down=True))
    r = client.post("/api/analyze", json=PROJECTOR)
    assert r.status_code == 200
    a = r.json()["analysis"]
    assert a["memory_notice"] == HS_DOWN and a["historical_matches"] == []
    stored = client.get(f"/api/incidents/{r.json()['incident_id']}").json()["incident"]
    assert stored["memory_synced"] is False  # kept locally, will sync later


def test_llm_failure_falls_back_safely(make_client):
    client = make_client(llm=FakeLLM(error=LLMError("rate limited", "rate_limit")))
    r = client.post("/api/analyze", json=PROJECTOR)
    assert r.status_code == 200
    a = r.json()["analysis"]
    assert a["confidence"] <= 0.3 and a["recommended_actions"]
    assert any("unavailable" in w.lower() for w in a["warnings"])


def test_failed_attempt_memory_blocks_repeat_recommendations(make_client):
    llm = FakeLLM({**LLM_DEFAULT, "confidence": 0.9, "relevant_incident_ids": ["INC-001"],
                   "recommended_actions": ["Restart the projector", "Replace HDMI cable"]})
    client = make_client(llm=llm, seed=True)
    a = client.post("/api/analyze", json=PROJECTOR).json()["analysis"]
    assert "INC-001" in [m["incident_id"] for m in a["historical_matches"]]
    assert "Restarted projector" in [f["action"] for f in a["failed_attempts"]]
    assert "Restart the projector" not in a["recommended_actions"]
    assert "Replace HDMI cable" in a["recommended_actions"]
    assert any("previously attempted" in w for w in a["warnings"])
    assert "Restarted projector" in llm.prompts[0]  # failed attempts were sent to the LLM
    assert a["deployment_correlation"]["checked"] is False  # physical incident: N/A


# ---------------------------------------------------------------- feedback loop
def test_feedback_is_learned_and_retrieved_by_future_incident(make_client):
    llm = FakeLLM()
    client = make_client(llm=llm)
    first = client.post("/api/analyze", json=PROJECTOR).json()["incident_id"]
    r = client.post("/api/feedback", json={
        "incident_id": first, "useful": True, "rating": 5, "action_taken": "Replaced HDMI cable",
        "result": "Projector started working", "actual_root_cause": "Damaged HDMI cable",
        "failed_attempts": ["Restarted projector"]})
    assert r.status_code == 200 and r.json()["stored_in_memory"] and r.json()["incident_status"] == "resolved"
    assert any("Engineer feedback" in i["content"] for i in client.hs.items)

    llm.response = {**LLM_DEFAULT, "relevant_incident_ids": [first],
                    "recommended_actions": ["Restart projector", "Replace HDMI cable"]}
    second = client.post("/api/analyze", json=PROJECTOR).json()["analysis"]
    m = second["historical_matches"][0]
    assert m["incident_id"] == first and m["previous_root_cause"] == "Damaged HDMI cable"
    assert m["successful_fix"] == "Replaced HDMI cable" and "Restarted projector" in m["failed_attempts"]
    assert "Restart projector" not in second["recommended_actions"]


def test_feedback_validation_and_unknown_incident(make_client):
    client = make_client()
    body = {"incident_id": "INC-999", "useful": True, "rating": 5, "action_taken": "x", "result": "y"}
    assert client.post("/api/feedback", json=body).status_code == 404
    assert client.post("/api/feedback", json={**body, "rating": 9}).status_code == 422


# ---------------------------------------------------------------- memory endpoints
def test_memory_search_and_get(make_client):
    client = make_client(seed=True)
    r = client.get("/api/memory/search", params={"q": "projector no signal"})
    assert r.status_code == 200 and r.json()["count"] >= 1
    assert r.json()["results"][0]["incident_id"] == "INC-001"
    detail = client.get("/api/memory/INC-001").json()
    assert detail["incident"]["root_cause"] == "Damaged HDMI cable" and detail["related_memories"]
    assert client.get("/api/memory/INC-999").status_code == 404
    assert client.get("/api/memory/search").status_code == 400


def test_memory_search_when_hindsight_down(make_client):
    client = make_client(hindsight=FakeHindsight(down=True))
    body = client.get("/api/memory/search", params={"q": "projector"}).json()
    assert body["success"] and body["memory_available"] is False and body["notice"] == HS_DOWN


def test_incident_endpoints_return_stored_data(make_client):
    client = make_client(seed=True)
    ids = [i["incident_id"] for i in client.get("/api/incidents").json()["incidents"]]
    assert {"INC-001", "INC-104"} <= set(ids)
    assert client.get("/api/incidents/INC-104").json()["incident"]["root_cause"] is None
    assert client.get("/api/incidents/NOPE").status_code == 404


# ---------------------------------------------------------------- deployments
def test_deployment_correlation_is_not_causation(make_client):
    client = make_client()
    dc = client.post("/api/analyze", json=API_500).json()["analysis"]["deployment_correlation"]
    assert dc["detected"] and dc["mock_mode"]
    assert dc["deployment"]["version"] == "v1.8.4" and dc["commit_sha"] == "8f72abc"
    assert dc["pull_request"]["number"] == 142
    assert "Potential deployment correlation detected" in dc["message"]
    assert "not proof of causation" in dc["message"]
    assert "caused the incident" not in dc["message"].lower()


def test_deployment_endpoints(make_client):
    client = make_client()
    r = client.get("/api/deployments/recent").json()
    assert r["mock_mode"] and len(r["deployments"]) == 3
    assert client.get("/api/deployments/dep-1084").json()["deployment"]["version"] == "v1.8.4"
    assert client.get("/api/deployments/nope").status_code == 404


def test_rollback_requires_explicit_confirmation(make_client):
    client = make_client()
    url = "/api/deployments/dep-1084/rollback"
    assert client.post(url, json={"reason": "500s after release"}).status_code == 400
    wrong = {"confirm": True, "confirm_deployment_id": "dep-1083", "reason": "500s after release"}
    assert client.post(url, json=wrong).status_code == 400
    assert client.get("/api/deployments/dep-1084").json()["deployment"]["status"] == "live"  # untouched


def test_mock_rollback_is_tracked_and_remembered(make_client):
    client = make_client()
    inc = client.post("/api/analyze", json=API_500).json()["incident_id"]
    url = "/api/deployments/dep-1084/rollback"
    body = {"confirm": True, "confirm_deployment_id": "dep-1084", "reason": "500s after release", "incident_id": inc}
    r = client.post(url, json=body)
    assert r.status_code == 200
    p = r.json()
    assert p["success"] and p["mock_mode"] and "MOCK" in p["notice"]
    assert p["rollback"]["status"] == "success" and p["rollback"]["rollback_id"].startswith("RB-")
    assert any("Rollback event" in i["content"] for i in client.hs.items)
    assert len(client.get(f"/api/incidents/{inc}").json()["incident"]["rollback_ids"]) == 1
    assert client.get("/api/deployments/dep-1084").json()["deployment"]["status"] == "rolled_back"
    assert client.post(url, json=body).status_code == 502  # already rolled back


# ---------------------------------------------------------------- github webhook
def _sign(body: bytes, secret: str = "s3cret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest()


def test_webhook_rejects_bad_or_missing_signature(make_client):
    client = make_client()
    body = b'{"zen": "x"}'
    h = {"X-GitHub-Event": "ping", "Content-Type": "application/json"}
    assert client.post("/api/github/webhook", content=body, headers=h).status_code == 401
    assert client.post("/api/github/webhook", content=body,
                       headers={**h, "X-Hub-Signature-256": _sign(body, "wrong")}).status_code == 401


def test_webhook_accepts_signed_events_and_stores_deployments(make_client):
    client = make_client()
    payload = {"repository": {"full_name": "o/r"}, "deployment": {
        "id": 7, "sha": "8f72abc", "environment": "production", "ref": "main", "created_at": "2026-08-14T14:27:00Z"}}
    body = json.dumps(payload).encode()
    h = {"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "deployment", "Content-Type": "application/json"}
    r = client.post("/api/github/webhook", content=body, headers=h)
    assert r.status_code == 200 and r.json()["recorded"] and r.json()["memory_stored"]
    assert any("8f72abc" in i["content"] for i in client.hs.items)
    ping = b"{}"
    r = client.post("/api/github/webhook", content=ping,
                    headers={**h, "X-Hub-Signature-256": _sign(ping), "X-GitHub-Event": "ping"})
    assert r.json()["message"] == "pong"


def test_webhook_refuses_when_secret_not_configured(make_client):
    client = make_client(secret="")
    assert client.post("/api/github/webhook", content=b"{}", headers={"X-GitHub-Event": "push"}).status_code == 503
