"""Turns a raw incident into normalized signals: error codes, keywords, tags,
infrastructure component, and whether deployment correlation even applies."""
import re
from dataclasses import dataclass, field
from datetime import datetime

from .models import ensure_utc, utcnow
from .schemas import IncidentInput

STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "to", "of", "in", "on", "for",
    "with", "not", "no", "but", "it", "its", "this", "that", "be", "has", "have", "had", "at",
    "by", "from", "as", "when", "after", "before", "cannot", "can", "unable",
    "production", "incident", "error", "errors", "status", "severity", "environment",
}

PHYSICAL_HINTS = {
    "projector", "hdmi", "vga", "cable", "monitor", "printer", "wifi", "router", "switch",
    "ups", "aircon", "cooling", "power", "sensor", "cctv", "keyboard", "mouse", "speaker",
    "microphone", "display", "classroom", "lab", "hardware",
}
SOFTWARE_HINTS = {
    "api", "http", "https", "endpoint", "backend", "frontend", "server", "database", "latency",
    "timeout", "exception", "deploy", "deployment", "pod", "kubernetes", "docker", "container",
    "microservice", "authentication", "auth", "login", "middleware", "cache", "queue",
    "release", "build", "stacktrace", "traceback",
}
COMPONENT_RULES = [
    ("Display / AV equipment", {"projector", "hdmi", "vga", "display", "monitor", "speaker", "microphone"}),
    ("Network", {"wifi", "router", "switch", "dns", "lan", "vpn", "bandwidth"}),
    ("Authentication service", {"auth", "authentication", "login", "oauth", "token", "session"}),
    ("Database", {"database", "db", "sql", "postgres", "mysql", "mongo", "redis"}),
    ("API / Backend", {"api", "endpoint", "http", "backend", "server", "500", "502", "503", "504"}),
    ("Power / Environment", {"power", "ups", "aircon", "cooling", "generator"}),
]

_HTTP_CODE = re.compile(r"\b([45]\d{2})\b")


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1 and t not in STOPWORDS]


def overlap_score(query_tokens: list[str], text: str) -> float:
    """Fraction of query tokens that appear in `text` (0..1)."""
    q = set(query_tokens)
    if not q:
        return 0.0
    return len(q & set(tokenize(text))) / len(q)


@dataclass
class ParsedIncident:
    title: str
    service: str
    error: str
    symptoms: str
    impact: str
    environment: str
    severity: str
    occurred_at: datetime
    logs: list[str]
    error_codes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    infrastructure_component: str = "Unknown"
    is_software: bool = False
    search_query: str = ""


def parse_incident(incident: IncidentInput) -> ParsedIncident:
    blob = " ".join([incident.title, incident.service, incident.error, incident.symptoms, *incident.logs])
    tokens = tokenize(blob)
    token_set = set(tokens)
    codes = sorted(set(_HTTP_CODE.findall(blob)))

    component = incident.service
    best = 0
    for name, words in COMPONENT_RULES:
        hits = len(words & (token_set | set(codes)))
        if hits > best:
            best, component = hits, name

    physical = len(PHYSICAL_HINTS & token_set)
    software = len(SOFTWARE_HINTS & token_set) + sum(1 for c in codes if c.startswith("5"))
    is_software = software > 0 and software >= physical

    keywords = list(dict.fromkeys(tokens))[:15]
    slug = re.sub(r"[^a-z0-9]+", "-", incident.service.lower()).strip("-")
    tags = [f"service:{slug}", f"env:{incident.environment}", f"severity:{incident.severity.value}"]
    tags += [f"http:{c}" for c in codes]

    search_query = " ".join([incident.title, incident.service, incident.error, incident.symptoms[:250]])
    occurred = ensure_utc(incident.occurred_at) if incident.occurred_at else utcnow()

    return ParsedIncident(
        title=incident.title, service=incident.service, error=incident.error,
        symptoms=incident.symptoms, impact=incident.impact, environment=incident.environment,
        severity=incident.severity.value, occurred_at=occurred, logs=incident.logs,
        error_codes=codes, keywords=keywords, tags=tags,
        infrastructure_component=component, is_software=is_software, search_query=search_query,
    )
