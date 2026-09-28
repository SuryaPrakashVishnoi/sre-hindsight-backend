"""Domain records + a small thread-safe JSON repository.

Hindsight is the semantic memory (recall by meaning). The repository is the
authoritative structured store: it powers /api/incidents, keeps working when
Hindsight is down, and tracks which records still need syncing to Hindsight.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class ActionRecord(BaseModel):
    action: str
    outcome: str  # successful | failed | rollback_executed
    notes: str = ""
    recorded_at: datetime = Field(default_factory=utcnow)


class FeedbackRecord(BaseModel):
    recommendation: str = ""
    action_taken: str
    result: str
    actual_root_cause: str = ""
    rating: int
    useful: bool
    recorded_at: datetime = Field(default_factory=utcnow)


class Relationship(BaseModel):
    type: str  # similar_to | possibly_correlated_with_deployment | ...
    target: str


class IncidentRecord(BaseModel):
    incident_id: str
    title: str
    service: str
    error: str
    symptoms: str
    impact: str = ""
    environment: str = "production"
    severity: str = "medium"
    status: str = "open"  # open | mitigated | resolved
    # Only engineer-verified root causes live here. AI guesses go in `ai_analysis`.
    root_cause: str | None = None
    resolution: str | None = None
    successful_actions: list[ActionRecord] = Field(default_factory=list)
    failed_actions: list[ActionRecord] = Field(default_factory=list)
    deployment: str | None = None  # version
    deployment_id: str | None = None
    commit: str | None = None
    pull_request: str | None = None
    deployment_correlation: dict[str, Any] | None = None
    engineer_feedback: list[FeedbackRecord] = Field(default_factory=list)
    confidence: float | None = None
    timestamp: datetime = Field(default_factory=utcnow)
    logs: list[str] = Field(default_factory=list)
    related_incidents: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    infrastructure_component: str | None = None
    relationships: list[Relationship] = Field(default_factory=list)
    ai_analysis: dict[str, Any] | None = None  # unverified hypothesis
    rollback_ids: list[str] = Field(default_factory=list)
    source: str = "live"  # live | seed
    memory_synced: bool = False


class RollbackRecord(BaseModel):
    rollback_id: str
    deployment_id: str
    version: str | None = None
    service: str | None = None
    environment: str | None = None
    incident_id: str | None = None
    reason: str
    requested_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    status: str = "pending"  # pending | success | failed
    result: str = ""
    provider: str | None = None
    mock: bool = False


class GitHubEventRecord(BaseModel):
    delivery_id: str | None = None
    event_type: str
    summary: dict[str, Any]
    received_at: datetime = Field(default_factory=utcnow)


class Repository:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._reserved: set[str] = set()
        self._incidents = {r["incident_id"]: IncidentRecord(**r) for r in self._read("incidents.json")}
        self._rollbacks = {r["rollback_id"]: RollbackRecord(**r) for r in self._read("rollbacks.json")}
        self._events = [GitHubEventRecord(**r) for r in self._read("github_events.json")]

    # ---- file helpers ----
    def _read(self, name: str) -> list[dict]:
        path = self.dir / name
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write(self, name: str, items: list[BaseModel]) -> None:
        path = self.dir / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([i.model_dump(mode="json") for i in items], indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ---- incidents ----
    def reserve_incident_id(self) -> str:
        with self._lock:
            nums = [int(m.group(1)) for i in list(self._incidents) + list(self._reserved)
                    if (m := re.fullmatch(r"INC-(\d+)", i))]
            new_id = f"INC-{max(nums, default=0) + 1:03d}"
            self._reserved.add(new_id)
            return new_id

    def save_incident(self, record: IncidentRecord) -> None:
        with self._lock:
            self._incidents[record.incident_id] = record
            self._reserved.discard(record.incident_id)
            self._write("incidents.json", list(self._incidents.values()))

    def get_incident(self, incident_id: str) -> IncidentRecord | None:
        return self._incidents.get(incident_id)

    def list_incidents(self) -> list[IncidentRecord]:
        return sorted(self._incidents.values(), key=lambda r: ensure_utc(r.timestamp), reverse=True)

    def unsynced_incidents(self) -> list[IncidentRecord]:
        return [r for r in self._incidents.values() if not r.memory_synced]

    def count_incidents(self) -> int:
        return len(self._incidents)

    # ---- rollbacks ----
    def save_rollback(self, rb: RollbackRecord) -> None:
        with self._lock:
            self._rollbacks[rb.rollback_id] = rb
            self._write("rollbacks.json", list(self._rollbacks.values()))

    def get_rollback(self, rollback_id: str) -> RollbackRecord | None:
        return self._rollbacks.get(rollback_id)

    def list_rollbacks(self) -> list[RollbackRecord]:
        return sorted(self._rollbacks.values(), key=lambda r: ensure_utc(r.requested_at), reverse=True)

    # ---- github events (capped) ----
    def add_github_event(self, ev: GitHubEventRecord, cap: int = 200) -> None:
        with self._lock:
            self._events.append(ev)
            self._events = self._events[-cap:]
            self._write("github_events.json", self._events)

    def list_github_events(self) -> list[GitHubEventRecord]:
        return list(reversed(self._events))
