"""Pydantic request/response schemas (the contract with the React frontend)."""
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_ENVIRONMENTS = {"production", "staging", "development", "test", "qa"}


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# ---------- Incident ----------
class IncidentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=200)
    service: str = Field(min_length=2, max_length=120)
    error: str = Field(min_length=1, max_length=2000)
    symptoms: str = Field(min_length=3, max_length=4000)
    impact: str = Field(default="Not specified", max_length=2000)
    environment: str = "production"
    severity: Severity = Severity.medium
    occurred_at: datetime | None = None  # optional; defaults to "now"
    logs: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("environment")
    @classmethod
    def _valid_env(cls, v: str) -> str:
        v = v.lower()
        if v not in ALLOWED_ENVIRONMENTS:
            raise ValueError(f"environment must be one of {sorted(ALLOWED_ENVIRONMENTS)}")
        return v


# ---------- Deployment ----------
class Deployment(BaseModel):
    deployment_id: str
    version: str
    service: str
    environment: str = "production"
    commit: str | None = None
    pull_request: str | None = None
    timestamp: datetime
    status: str
    commit_message: str | None = None
    provider: str | None = None
    mock: bool = False


class PullRequestInfo(BaseModel):
    number: int
    title: str | None = None
    url: str | None = None


DEPLOYMENT_DISCLAIMER = "Correlation is not proof of causation."


class DeploymentCorrelation(BaseModel):
    checked: bool = False
    detected: bool = False
    strength: str | None = None  # close | moderate | distant
    mock_mode: bool = False
    message: str = "Deployment correlation was not evaluated."
    disclaimer: str = DEPLOYMENT_DISCLAIMER
    incident_time: datetime | None = None
    minutes_before_incident: float | None = None
    deployment: Deployment | None = None
    commit_sha: str | None = None
    commit_message: str | None = None
    pull_request: PullRequestInfo | None = None
    error: str | None = None


# ---------- Analysis ----------
class HistoricalMatch(BaseModel):
    incident_id: str
    relevance: float = Field(ge=0, le=1)
    title: str | None = None
    service: str | None = None
    what_happened: str
    previous_root_cause: str | None = None
    successful_fix: str | None = None
    failed_attempts: list[str] = Field(default_factory=list)
    verified: bool = False


class FailedAttemptItem(BaseModel):
    action: str
    outcome: str = "failed"
    source_incident_id: str | None = None
    note: str = ""


class Analysis(BaseModel):
    summary: str
    historical_matches: list[HistoricalMatch] = Field(default_factory=list)
    root_cause: str
    failed_attempts: list[FailedAttemptItem] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    prevention_steps: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    why_recommended: str
    deployment_correlation: DeploymentCorrelation = Field(default_factory=DeploymentCorrelation)
    memory_notice: str | None = None
    warnings: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    success: bool = True
    incident_id: str
    analysis: Analysis


# ---------- Feedback ----------
class FeedbackInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    incident_id: str = Field(min_length=3)
    useful: bool
    rating: int = Field(ge=1, le=5)
    action_taken: str = Field(min_length=1, max_length=2000)
    result: str = Field(min_length=1, max_length=2000)
    actual_root_cause: str = Field(default="", max_length=2000)
    # optional extras that make failed-attempt memory explicit
    resolved: bool | None = None  # if omitted, inferred from `result`
    failed_attempts: list[str] = Field(default_factory=list, max_length=20)


class FeedbackResponse(BaseModel):
    success: bool = True
    incident_id: str
    incident_status: str
    stored_in_memory: bool
    notice: str | None = None


# ---------- Memory ----------
class MemoryHit(BaseModel):
    memory_id: str | None = None
    incident_id: str | None = None
    kind: str | None = None
    text: str
    relevance: float = Field(default=0.0, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------- Rollback ----------
class RollbackRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    confirm: bool = False
    confirm_deployment_id: str = ""  # must equal the deployment id in the URL
    reason: str = Field(min_length=5, max_length=1000)
    incident_id: str | None = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    fallback: str | None = None
    details: Any = None
