"""SREIncidentAgent: parse -> search memory -> build context -> check deployments ->
one LLM call -> structured analysis -> save. Also learns from feedback and rollbacks."""
import json
import logging
import re
from collections import defaultdict

from .alert_parser import ParsedIncident, parse_incident, tokenize
from .deployment_service import DeploymentService
from .llm_client import LLMClient, LLMError
from .memory_manager import HISTORY_UNAVAILABLE_NOTICE, NO_HISTORY_NOTICE, MemoryManager, MemorySearchResult
from .models import (
    ActionRecord, FeedbackRecord, IncidentRecord, Relationship, Repository, RollbackRecord, utcnow,
)
from .schemas import (
    Analysis, AnalyzeResponse, DeploymentCorrelation, FailedAttemptItem, FeedbackInput,
    FeedbackResponse, HistoricalMatch, IncidentInput,
)

logger = logging.getLogger("sre.agent")

FAILED_WARNING = "These troubleshooting steps were previously attempted and did not resolve the issue."
MIN_RELEVANCE = 0.2
MAX_MATCHES = 3
SEARCH_LIMIT = 8
LLM_FALLBACK_MESSAGE = "AI analysis is temporarily unavailable. Showing evidence from memory and deployment checks only."


class IncidentNotFoundError(Exception):
    pass


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _clip(text: str, n: int = 260) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


class SREIncidentAgent:
    def __init__(self, memory: MemoryManager, llm: LLMClient,
                 deployments: DeploymentService | None = None, repo: Repository | None = None):
        self.memory = memory
        self.llm = llm
        self.deployments = deployments
        self.repo = repo if repo is not None else memory.repo

    # ================================================================ analyze
    async def analyze(self, incident: IncidentInput) -> AnalyzeResponse:
        logger.info("Incident received: service=%s severity=%s env=%s", incident.service, incident.severity.value, incident.environment)
        parsed = parse_incident(incident)
        incident_id = self.repo.reserve_incident_id()

        # 1. memory
        search = await self.memory.search_memories(parsed.search_query, limit=SEARCH_LIMIT)
        logger.info("Memory search done: available=%s hits=%d", search.available, len(search.hits))
        matches, snippets = self._collect_history(search)
        logger.info("Historical match count: %d", len(matches))

        # 2. deployments
        correlation = await self._check_deployments(parsed)

        # 3. one LLM call
        prompt = self._build_prompt(parsed, matches, snippets, correlation, search)
        analysis = ""
        llm_ok = False
        warnings: list[str] = []
        logger.info("LLM request for %s (prompt chars=%d)", incident_id, len(prompt))
        try:
            analysis = await self.llm.generate_response(prompt, json_mode=True)
            llm_ok = True
        except LLMError as exc:
            logger.warning("LLM failure (%s): %s", exc.kind, exc)
            analysis = LLM_FALLBACK_MESSAGE
            warnings.append(f"{LLM_FALLBACK_MESSAGE} ({exc.kind})")
        except Exception:  # defensive: analysis must always continue
            logger.exception("Unexpected LLM failure")
            analysis = LLM_FALLBACK_MESSAGE
            warnings.append(LLM_FALLBACK_MESSAGE)

        # 4. structure
        result = self._structure(analysis, llm_ok, matches, correlation, search, warnings)
        logger.info("Recommendation generated for %s: confidence=%.2f actions=%d", incident_id, result.confidence, len(result.recommended_actions))

        # 5. save (AI output is stored as an UNVERIFIED hypothesis, never as root_cause)
        await self._save_incident(incident_id, parsed, result, correlation)
        return AnalyzeResponse(success=True, incident_id=incident_id, analysis=result)

    # --------------------------------------------------------------- history
    def _collect_history(self, search: MemorySearchResult) -> tuple[list[HistoricalMatch], list[str]]:
        by_inc: dict[str, float] = defaultdict(float)
        first_text: dict[str, str] = {}
        snippets: list[str] = []
        for h in search.hits:
            if h.incident_id:
                by_inc[h.incident_id] = max(by_inc[h.incident_id], h.relevance)
                first_text.setdefault(h.incident_id, h.text)
            elif len(snippets) < 3:
                snippets.append(_clip(h.text, 240))

        matches: list[HistoricalMatch] = []
        for inc_id, rel in sorted(by_inc.items(), key=lambda kv: kv[1], reverse=True):
            if rel < MIN_RELEVANCE:
                continue
            rec = self.repo.get_incident(inc_id)
            if rec:
                fix = rec.resolution or (rec.successful_actions[0].action if rec.successful_actions else None)
                matches.append(HistoricalMatch(
                    incident_id=inc_id, relevance=rel, title=rec.title, service=rec.service,
                    what_happened=_clip(f"{rec.error} — {rec.symptoms}"),
                    previous_root_cause=rec.root_cause, successful_fix=fix,
                    failed_attempts=[a.action for a in rec.failed_actions],
                    verified=bool(rec.root_cause) and rec.status in {"resolved", "mitigated"},
                ))
            else:  # only known from Hindsight text; expose just what the memory said
                matches.append(HistoricalMatch(incident_id=inc_id, relevance=rel, what_happened=_clip(first_text[inc_id])))
            if len(matches) >= MAX_MATCHES:
                break
        return matches, snippets

    def _failed_from(self, matches: list[HistoricalMatch]) -> list[FailedAttemptItem]:
        seen, out = set(), []
        for m in matches:
            for action in m.failed_attempts:
                key = (m.incident_id, _norm(action))
                if key not in seen:
                    seen.add(key)
                    out.append(FailedAttemptItem(action=action, source_incident_id=m.incident_id,
                                                 note=f"Previously attempted in {m.incident_id} and did not resolve the issue."))
        return out

    # ------------------------------------------------------------ deployment
    async def _check_deployments(self, p: ParsedIncident) -> DeploymentCorrelation:
        if not self.deployments:
            return DeploymentCorrelation(message="No deployment provider is configured.")
        if not p.is_software:
            return DeploymentCorrelation(message="Deployment correlation does not apply to this type of incident.")
        try:
            return await self.deployments.correlate(incident_time=p.occurred_at, service=p.service, environment=p.environment)
        except Exception as exc:
            logger.warning("Deployment correlation failed: %s", type(exc).__name__)
            return DeploymentCorrelation(error=type(exc).__name__, message="Deployment correlation could not be evaluated.")

    # ---------------------------------------------------------------- prompt
    def _build_prompt(self, p: ParsedIncident, matches: list[HistoricalMatch], snippets: list[str],
                      corr: DeploymentCorrelation, search: MemorySearchResult) -> str:
        history = []
        for m in matches:
            rec = self.repo.get_incident(m.incident_id)
            history.append({
                "incident_id": m.incident_id, "relevance": m.relevance, "title": m.title, "what_happened": m.what_happened,
                "verified_root_cause": m.previous_root_cause, "successful_fix": m.successful_fix,
                "failed_attempts_DO_NOT_REPEAT": m.failed_attempts,
                "engineer_feedback": [{"action": f.action_taken, "result": f.result, "rating": f.rating}
                                      for f in (rec.engineer_feedback[-2:] if rec else [])],
            })
        ctx = {
            "current_incident": {"title": p.title, "service": p.service, "error": p.error, "symptoms": p.symptoms,
                                 "impact": p.impact, "environment": p.environment, "severity": p.severity,
                                 "component": p.infrastructure_component, "logs": [_clip(x, 200) for x in p.logs[:5]]},
            "memory_status": ("unavailable" if not search.available else "ok" if history else "no relevant history"),
            "historical_incidents": history,
            "other_memory_snippets": snippets,
            "deployment_check": corr.model_dump(mode="json", include={"checked", "detected", "strength", "message", "minutes_before_incident", "commit_sha", "commit_message", "pull_request"})
            | ({"version": corr.deployment.version} if corr.deployment else {}),
        }
        notice = ""
        if not search.available:
            notice = f"\nHistorical memory is unavailable. State clearly: \"{HISTORY_UNAVAILABLE_NOTICE}\"\n"
        elif not history:
            notice = f"\nNo relevant history exists. State clearly: \"{NO_HISTORY_NOTICE}\" Do NOT invent past incidents.\n"
        return (
            "Analyze this incident using ONLY the context below.\n"
            f"{notice}"
            "Rules:\n"
            "- Historical facts come only from `historical_incidents`. Never invent incidents, IDs or fixes.\n"
            "- Do not recommend anything listed in failed_attempts_DO_NOT_REPEAT unless you cite new evidence.\n"
            "- A deployment correlation is NOT proof of causation; never say a deployment caused the incident.\n"
            "- In `why_recommended`, write two labelled parts: 'Historical evidence:' and 'Current inference:'.\n"
            "- `root_cause` is your hypothesis: begin it with 'Hypothesis:'.\n"
            "- `relevant_incident_ids` must list only historical incidents that truly match this incident.\n"
            "- confidence is 0..1 and must reflect how much evidence exists.\n\n"
            f"CONTEXT:\n{json.dumps(ctx, default=str, indent=1)}\n\n"
            "Respond with ONLY a JSON object with keys: summary (string), root_cause (string), "
            "recommended_actions (array of short strings, ordered), prevention_steps (array of strings), "
            "confidence (number), why_recommended (string), relevant_incident_ids (array of strings)."
        )

    # ------------------------------------------------------------- structure
    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            i, j = raw.find("{"), raw.rfind("}")
            if i == -1 or j <= i:
                return None
            try:
                data = json.loads(raw[i : j + 1])
            except json.JSONDecodeError:
                return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _strs(value) -> list[str]:
        return [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else []

    def _structure(self, raw: str, llm_ok: bool, matches: list[HistoricalMatch], corr: DeploymentCorrelation,
                   search: MemorySearchResult, warnings: list[str]) -> Analysis:
        data = self._parse_json(raw) if llm_ok else None
        if llm_ok and data is None:
            warnings.append("The AI response was not valid JSON; showing it as plain text.")

        # keep only matches the LLM judged relevant (when it told us)
        if data is not None and isinstance(data.get("relevant_incident_ids"), list):
            keep = set(self._strs(data["relevant_incident_ids"]))
            matches = [m for m in matches if m.incident_id in keep]

        failed = self._failed_from(matches)
        if failed:
            warnings.append(FAILED_WARNING)

        if data is not None:
            actions = self._drop_repeated_failures(self._strs(data.get("recommended_actions")), failed, warnings)
            try:
                conf = float(data.get("confidence", 0.4))
            except (TypeError, ValueError):
                conf = 0.4
            has_evidence = any(m.verified for m in matches) or corr.detected
            conf = max(0.0, min(conf, 1.0 if has_evidence else 0.6))  # no evidence => cap confidence
            summary = str(data.get("summary") or "").strip() or "Analysis completed."
            root = str(data.get("root_cause") or "").strip() or "Hypothesis: root cause could not be determined from the available information."
            why = str(data.get("why_recommended") or "").strip() or "Based on the current incident details."
            prevention = self._strs(data.get("prevention_steps"))
        elif llm_ok:  # LLM answered but not as JSON
            conf, actions, prevention = 0.3, [], []
            summary, root, why = _clip(raw, 600), "Hypothesis: see summary (unstructured AI response).", "Unstructured AI response."
        else:
            summary, root, actions, conf, why = self._fallback(matches)
            prevention = []

        if not search.available:
            notice = HISTORY_UNAVAILABLE_NOTICE
        elif not matches:
            notice = NO_HISTORY_NOTICE
        else:
            notice = None
        return Analysis(
            summary=summary, historical_matches=matches, root_cause=root, failed_attempts=failed,
            recommended_actions=actions, prevention_steps=prevention, confidence=round(conf, 2),
            why_recommended=why, deployment_correlation=corr, memory_notice=notice, warnings=warnings,
        )

    @staticmethod
    def _stems(text: str) -> set[str]:
        out = set()
        for t in tokenize(text):
            for _ in range(2):
                if len(t) > 4:
                    t = re.sub(r"(ing|ed|es|s|e)$", "", t)
            out.add(t)
        return out

    def _drop_repeated_failures(self, actions: list[str], failed: list[FailedAttemptItem], warnings: list[str]) -> list[str]:
        failed_sets = [s for s in (self._stems(f.action) for f in failed) if s]
        kept = []
        for a in actions:
            sa = self._stems(a)
            repeated = sa and any(len(sa & fs) / min(len(sa), len(fs)) >= 0.75 for fs in failed_sets)
            if repeated and "new evidence" not in a.lower():
                logger.info("Dropped recommendation that repeats a failed attempt: %s", a)
                warnings.append(f"Removed '{a}' because a similar step failed in a previous incident.")
            else:
                kept.append(a)
        return kept

    @staticmethod
    def _fallback(matches: list[HistoricalMatch]) -> tuple[str, str, list[str], float, str]:
        top = matches[0] if matches else None
        if top and top.previous_root_cause:
            root = (f"Not determined by AI. Historical incident {top.incident_id} (relevance {top.relevance}) had verified root cause: "
                    f"{top.previous_root_cause}. Verify whether it applies here.")
            actions = [f"(from {top.incident_id}) {top.successful_fix}"] if top.successful_fix else []
            conf = 0.3
        else:
            root = "Not determined: AI analysis is unavailable and no verified historical root cause was found."
            actions = []
            conf = 0.15
        actions.append("Collect logs and current metrics, then re-run the analysis when the AI service is available.")
        return (LLM_FALLBACK_MESSAGE, root, actions, conf,
                "Generated without the language model, directly from stored incident memory and deployment checks.")

    # ------------------------------------------------------------------ save
    async def _save_incident(self, incident_id: str, p: ParsedIncident, result: Analysis, corr: DeploymentCorrelation) -> None:
        rels = [Relationship(type="similar_to", target=m.incident_id) for m in result.historical_matches]
        rec = IncidentRecord(
            incident_id=incident_id, title=p.title, service=p.service, error=p.error, symptoms=p.symptoms,
            impact=p.impact, environment=p.environment, severity=p.severity, status="open",
            confidence=result.confidence, timestamp=p.occurred_at, logs=p.logs, tags=p.tags,
            related_incidents=[m.incident_id for m in result.historical_matches],
            infrastructure_component=p.infrastructure_component, relationships=rels,
            ai_analysis=result.model_dump(mode="json", exclude={"deployment_correlation", "historical_matches"}),
        )
        if corr.detected and corr.deployment:
            rec.deployment, rec.deployment_id = corr.deployment.version, corr.deployment.deployment_id
            rec.commit = corr.commit_sha
            rec.pull_request = f"#{corr.pull_request.number}" if corr.pull_request else corr.deployment.pull_request
            rec.deployment_correlation = corr.model_dump(mode="json")
            rec.relationships.append(Relationship(type="possibly_correlated_with_deployment", target=corr.deployment.version))
        self.repo.save_incident(rec)
        await self.memory.store_incident(rec)
        logger.info("Incident stored: %s", incident_id)

    # ============================================================== feedback
    @staticmethod
    def _infer_success(fb: FeedbackInput) -> bool:
        if fb.resolved is not None:
            return fb.resolved
        text = _norm(fb.result)
        negative = ("did not", "didn t", "didnt", "not work", "no effect", "still", "failed", "no change", "unresolved")
        return fb.useful and not any(n in text for n in negative)

    async def learn_from_feedback(self, fb: FeedbackInput) -> FeedbackResponse:
        rec = self.repo.get_incident(fb.incident_id)
        if rec is None:
            raise IncidentNotFoundError(fb.incident_id)
        logger.info("Feedback received for %s (rating=%s)", fb.incident_id, fb.rating)

        success = self._infer_success(fb)
        recs = (rec.ai_analysis or {}).get("recommended_actions") or []
        fbr = FeedbackRecord(recommendation="; ".join(recs[:3]), action_taken=fb.action_taken, result=fb.result,
                             actual_root_cause=fb.actual_root_cause, rating=fb.rating, useful=fb.useful)
        rec.engineer_feedback.append(fbr)

        if success:
            rec.successful_actions.append(ActionRecord(action=fb.action_taken, outcome="successful", notes=fb.result))
            rec.resolution, rec.status = fb.action_taken, "resolved"
        else:
            rec.failed_actions.append(ActionRecord(action=fb.action_taken, outcome="failed", notes=fb.result))
        extra_failed = [a for a in fb.failed_attempts if _norm(a) not in {_norm(x.action) for x in rec.failed_actions}]
        for a in extra_failed:
            rec.failed_actions.append(ActionRecord(action=a, outcome="failed"))
        if fb.actual_root_cause:
            rec.root_cause = fb.actual_root_cause
        self.repo.save_incident(rec)

        ok = await self.memory.store_feedback(rec, fbr)
        if success:
            ok = await self.memory.store_successful_experience(rec, fb.action_taken, fb.result) and ok
        else:
            ok = await self.memory.store_failed_attempt(rec, fb.action_taken, fb.result) and ok
        for a in extra_failed:
            ok = await self.memory.store_failed_attempt(rec, a) and ok
        synced = await self.memory.store_incident(rec)  # upsert with verified data
        logger.info("Feedback stored for %s: %s", fb.incident_id, ok and synced)
        return FeedbackResponse(
            incident_id=rec.incident_id, incident_status=rec.status, stored_in_memory=ok and synced,
            notice=None if (ok and synced) else "Feedback was saved locally and will sync to Hindsight when it is available.",
        )

    # ============================================================== rollback
    async def record_rollback(self, rb: RollbackRecord) -> bool:
        """Persist a rollback and remember it in Hindsight (linked to the incident if given)."""
        self.repo.save_rollback(rb)
        rec = self.repo.get_incident(rb.incident_id) if rb.incident_id else None
        if rec:
            rec.rollback_ids.append(rb.rollback_id)
            if rb.status == "success":
                rec.successful_actions.append(ActionRecord(
                    action=f"Rolled back deployment {rb.version or rb.deployment_id}", outcome="rollback_executed",
                    notes="Rollback executed. Incident resolution still needs engineer confirmation via feedback."))
                rec.status = "mitigated" if rec.status == "open" else rec.status
            else:
                rec.failed_actions.append(ActionRecord(action=f"Rollback of {rb.version or rb.deployment_id}", outcome="failed", notes=rb.result))
            self.repo.save_incident(rec)
        stored = await self.memory.store_rollback_event(rb, rec)
        if rec:
            await self.memory.store_incident(rec)
        return stored
