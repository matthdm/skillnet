# Skillnet Pipeline

A stateful LLM agent workflow that takes a feature specification, retrieves relevant skill patterns from a vector store, generates code, runs tests, and iterates until the tests pass — then opens a PR on GitHub.

The v1 input path is markdown feature ingestion. Live Rally polling is a v2 item.

---

## Current Capabilities (v1)

| Capability | Status |
|------------|--------|
| Markdown feature ingestion (`POST /ingest/feature/markdown`) | ✅ |
| Pre-parsed JSON ingestion (`POST /ingest/feature`) | ✅ |
| Duplicate detection (feature_id deduplication) | ✅ |
| LangGraph pipeline: analyze → retrieve → codegen → test → commit | ✅ |
| Self-correction loop (interpret failures → retry codegen, up to N iterations) | ✅ |
| Semantic skill retrieval via ChromaDB (1,431 skills indexed) | ✅ |
| Multi-provider LLM routing (Anthropic primary, OpenAI fallback) | ✅ |
| Per-node token tracking and cost estimation | ✅ |
| GitHub repo creation, feature branch, and PR (via REST API) | ✅ |
| Redis job state with LangGraph checkpoint | ✅ |
| Streamlit operator dashboard (6 pages) | ✅ |
| Admin skill ingestion endpoint with live progress | ✅ |
| Execution trace (per-node timing, provider, tokens, cost) | ✅ |

---

## Pipeline Flow

```
POST /ingest/feature/markdown
        │
        ▼
   FeatureSpec (parsed + deduplicated)
        │
        ▼
   JobState → Redis queue
        │
        ▼
┌───────────────────────────────────────┐
│         LangGraph Orchestrator        │
│                                       │
│  inject → analyze → retrieve          │
│                         │             │
│                      codegen          │
│                         │             │
│                       test ──── PASS → commit → PR
│                         │             │
│                        FAIL           │
│                         │             │
│                     interpret         │
│                         │             │
│                      codegen  (retry) │
│                         │             │
│               (up to max_iterations)  │
│                         │             │
│                     EXHAUSTED         │
└───────────────────────────────────────┘
```

**Terminal states:** `COMMITTED` · `EXHAUSTED` · `FAILED` · `PAUSED`

---

## Dashboard Pages

| Page | Purpose |
|------|---------|
| Jobs Queue | All jobs with status badges and quick stats |
| Job Detail | Progress bar, metrics (tokens/cost/time), repo + PR links, skills panel, execution trace, generated files |
| Skill Pool | Browse and search the ChromaDB skill collection |
| Degraded Jobs | Jobs that ran on a degraded/local LLM provider |
| Submit Feature | Submit a markdown feature spec directly from the browser |
| Admin | Trigger skill re-indexing with live progress bar |

Dashboard runs at `http://localhost:8501`.

---

## Requirements

- Docker Desktop (or Docker Engine + Compose plugin)
- Python 3.12+ 64-bit (for local dev / startup script)
- API keys in `pipeline/.env`:

```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GITHUB_TOKEN=          # scopes: repo, workflow
GITHUB_OWNER=          # your GitHub username
REDIS_URL=redis://redis:6379
```

---

## Quick Start

```powershell
powershell -ExecutionPolicy Bypass -File .\pipeline\scripts\startup.ps1
```

The script: detects 64-bit Python, creates/repairs the venv, checks Docker, runs `docker compose up --build`.

After startup:
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Dashboard: `http://localhost:8501`

**First run:** go to the Admin page and click **Run Ingestion** to embed and index the 1,431 skills (~5–15 min). The pipeline will run but retrieval quality is poor until indexing completes.

---

## Core API Endpoints

```
POST /ingest/feature/markdown     text/plain markdown body
POST /ingest/feature              JSON FeatureSpec

GET  /jobs                        list all jobs
GET  /jobs/{id}                   full job state
GET  /jobs/{id}/trace             execution trace only

GET  /admin/ingest-skills/status  ingestion progress
POST /admin/ingest-skills         trigger re-indexing

GET  /health                      {"status": "ok", "skills_indexed": N}
```

**409** is returned on duplicate `feature_id`. Clear with `DEL features:seen` in Redis to resubmit.

---

## Smoke Test

Submits all markdown fixtures from `tests/fixtures/` to the ingestion endpoint:

```powershell
python pipeline\scripts\smoke_test.py --base-url http://localhost:8000
```

---

## Submission Modes

Three modes are available from the **Submit Feature** page. Set via the **Job Type** selector.

### Feature
Builds a new isolated utility, library, or module. The pipeline creates a new GitHub repo, generates all files from scratch, and opens a PR. Use this when there is no existing codebase involved.

*Example: FEAT-004 — rate limiter library. Produces `rate_limiter/limiter.py`, `tests/test_limiter.py`, etc. in a new repo.*

### New Service
Builds a complete, independently deployable service. Same as Feature in that a new repo is created, but the analyze prompt is tuned to always produce a full scaffold: entry point, config module (`BaseSettings`), `Dockerfile`, `requirements.txt`, and `README.md` — even if the core feature logic is simple.

Use this when the output needs to run as a container, not just be imported.

*Example: FEAT-005 — task tracker HTTP API. Produces `main.py`, `config.py`, `Dockerfile`, `README.md`, plus the task routes and tests.*

### Change Request
Modifies an existing repo. You specify a **Target Repo** (the repo name under your GitHub account). Before codegen runs, the pipeline fetches the existing file tree so the LLM knows what's already there. It adds or modifies files in place and opens a PR against the existing repo — it does not create a new repo.

Use this when you want to extend or fix code that was previously generated (or already exists).

*Example: FEAT-006 — add `stats()` and `reset_all()` to FEAT-004's rate limiter. Target repo: `feat-004`. The pipeline reads the existing `limiter.py` before generating the changes.*

**Key distinction:** Feature and New Service generate from a blank slate. Change Request is context-aware — the LLM sees what already exists and must not duplicate or replace it.

---

## Test Fixtures

| Fixture | Description | Job Type |
|---------|-------------|---------|
| `feat-001-users-api.md` | Users REST API | feature |
| `feat-002-db-migration.md` | DB migration tooling | feature |
| `feat-003-jwt-auth.md` | JWT authentication | feature |
| `feat-004-rate-limiter.md` | In-memory token bucket rate limiter | feature |
| `feat-005-task-tracker-service.md` | Complete runnable task tracker HTTP service | new_service |
| `feat-006-rate-limiter-stats.md` | Add stats() and reset_all() to feat-004 | change_request → feat-004 |

---

## Skill Ingestion

Skills are embedded once (or on demand via Admin page) and stored in ChromaDB at `chroma_data/` (mounted as a Docker volume — survives restarts).

```
pipeline/scripts/ingest_skills.py   # standalone script
POST /admin/ingest-skills            # API trigger (preferred)
```

Source files at repo root: `skills_catalog.json` (1,431 skills), `skills_index.json`, `skills/*/SKILL.md`.

---

## Architecture Notes

- **ChromaDB** runs embedded inside the `api` container. No separate service.
- **Redis** holds all job state. Plain `redis:alpine` — no RediSearch module needed.
- **LangGraph** uses `AsyncRedisSaver` for checkpoint persistence between nodes.
- **Token tracking** uses a LangChain `BaseCallbackHandler` on every `with_structured_output` call. Cost is estimated from the provider label against Anthropic public pricing.
- **GitHub commits** use the GitHub Contents API (no local git). Works for any changeset that fits within the API's per-file limits.

Full architecture and decision log: [`DESIGN.md`](../DESIGN.md)

---

## Roadmap

### v1.1 (next)

| Item | Description |
|------|-------------|
| **Project registry** | Group features and jobs under named projects. Track which test cases have run, success rate, and total cost per project. ✅ Built |
| **Pre-codegen plan + approval** | Before any code is generated, the pipeline produces a lightweight plan: LLM's understanding of the requirements, intended files, and token/cost estimate. A human approves or rejects before codegen runs. See DESIGN.md Section 24. |
| **Failure recovery** | Resume PAUSED jobs from LangGraph checkpoint. Retry FAILED/EXHAUSTED jobs seeded with prior generated files and error context. Patch-retry with a manual fix hint. |
| **Submission modes** | `new_service` scaffold prompt and `change_request` existing-file-tree context. ✅ UI built; `inject_node` routing in progress. |
| **Job persistence** | Redis AOF + named Docker volume so job history survives `docker compose down/up`. |

### v2 (future)

| Item | Description |
|------|-------------|
| Multi-agent decomposition | Complex features decomposed into parallel sub-agents (core/tests/docs) via LangGraph `Send` API, bound by a shared interface contract. |
| Rally ingestion | Parser producing `FeatureSpec` from Rally REST API stories. |
| Docker-in-Docker sandboxing | Run generated code in an isolated container instead of a subprocess tempdir. |
| Complexity auto-classifier | Route simple features through single-agent path; complex features through multi-agent decomposition. |
