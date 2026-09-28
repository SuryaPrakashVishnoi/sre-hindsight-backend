# SRE Hindsight — Backend

AI incident response agent with persistent organizational memory.
FastAPI · Pydantic · Hindsight · Groq · GitHub API · httpx

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# open .env and fill in keys (all optional to start, see below)
uvicorn app.main:app --reload --port 8000
```

Docs: http://localhost:8000/docs · http://localhost:8000/redoc
Frontend (Vite) origin `http://localhost:5173` is already allowed by CORS.

### What works with which keys

| You set                                   | You get                                                        |
|-------------------------------------------|----------------------------------------------------------------|
| nothing                                   | API runs, incidents saved locally, fallback (non-AI) analysis   |
| `GROQ_API_KEY` (+ `GROQ_MODEL`)           | AI analysis                                                    |
| `HINDSIGHT_API_URL` (+ `HINDSIGHT_API_KEY`) | Persistent memory, similar-incident retrieval, learning loop |
| `GITHUB_WEBHOOK_SECRET`                   | Signed GitHub webhook endpoint                                 |
| `GITHUB_TOKEN` + `GITHUB_REPOSITORY`      | Commit / PR lookup during deployment correlation               |
| `MOCK_DEPLOYMENTS=false` + Render or Vercel creds | Real deployment history and rollback                   |

`MOCK_DEPLOYMENTS=true` (default) needs no credentials and never touches production.
`GET /api/status` shows what is configured (never returns secrets).

## Endpoints

```
GET  /api/health
GET  /api/status
POST /api/analyze
POST /api/feedback
GET  /api/incidents            GET /api/incidents/{id}
GET  /api/memory/search        GET /api/memory/{id}
GET  /api/deployments/recent   GET /api/deployments/{id}
POST /api/deployments/{id}/rollback
POST /api/github/webhook
```

Try it:

```bash
curl -X POST localhost:8000/api/analyze -H 'Content-Type: application/json' -d '{
  "title": "Classroom Projector Has No Signal",
  "service": "Classroom Infrastructure",
  "error": "No Signal",
  "symptoms": "Projector is powered but laptop display is not appearing",
  "impact": "Faculty cannot conduct the presentation",
  "environment": "production",
  "severity": "high"}'
```

Rollback requires explicit confirmation:

```json
{ "confirm": true, "confirm_deployment_id": "dep-1084", "reason": "500s after release", "incident_id": "INC-105" }
```

Errors always look like `{"success": false, "error": "...", "fallback": "..."}`.

## Architecture

```
main.py              routes, error handlers, wiring (Container)
incident_agent.py    SREIncidentAgent: parse → memory → deployments → 1 LLM call → structure → save
memory_manager.py    Hindsight (httpx): retain / recall, never raises when Hindsight is down
llm_client.py        Groq: generate_response(prompt) is the only public interface
alert_parser.py      error codes, keywords, component, "is deployment correlation relevant?"
deployment_service.py  DeploymentProvider → Mock / Render / Vercel, correlation, rollback
github_service.py    commit/PR/deployment/release lookups + HMAC webhook verification
models.py            stored records + thread-safe JSON repository (data/*.json)
schemas.py           request/response contract for the frontend
```

## Design decisions worth knowing

- **Hindsight + a local JSON store.** Hindsight is semantic memory (recall by meaning). `data/*.json`
  is the structured source of truth for `/api/incidents`, and lets incidents created while Hindsight was
  down sync later (`memory_synced` flag, retried at startup).
- **AI guesses are never stored as facts.** A new incident stores the AI output as an *unverified
  hypothesis*. `root_cause` is only set from engineer feedback (`actual_root_cause`). Otherwise the agent
  would eventually cite its own guesses as "history".
- **Failed-attempt memory.** Feedback records what failed; future prompts include those steps as
  "do not repeat", and any recommendation that matches a failed step is removed unless it cites new evidence.
- **Correlation ≠ causation.** Deployment matches say "Potential deployment correlation detected",
  always carry a disclaimer, and are skipped for non-software incidents (e.g. a projector).
- **Relevance score is a heuristic.** Hindsight recall returns ranked results without a portable score,
  so `relevance` = 15% rank + 85% keyword overlap, and the LLM additionally prunes to truly relevant incidents.
- **Confidence is capped at 0.6** when there is no verified history and no deployment correlation.
- **Seed data** (`data/seed_incidents.json`: INC-001, INC-104) loads only when `SEED_DEMO_DATA=true`
  and `APP_ENV != production`. INC-104 is modelled as a *potential* correlation (root cause unconfirmed).

## Verify before demo day

- I could not run the tests or call live services in my build environment (no network / packages),
  so run `pytest` once after installing. External APIs are mocked; no credentials needed.
- Hindsight REST paths used (`/v1/default/banks/{bank}/memories`, `/memories/recall`, `/profile`, `/health`)
  follow their docs; if your Hindsight version differs, they're isolated in `memory_manager.py`.
- Render/Vercel calls follow their public APIs but were not exercised against real accounts.

## Security

Secrets come only from `.env` / environment, `.env` is git-ignored, no secret is ever logged (httpx logging
is muted), and webhook signatures are verified with constant-time HMAC-SHA256; with no
`GITHUB_WEBHOOK_SECRET` set, the webhook refuses all requests.
