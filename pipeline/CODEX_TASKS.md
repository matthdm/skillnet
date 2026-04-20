# Codex Task Queue

> Tasks for Codex to implement while Claude is between sessions.
> Read DESIGN.md and AGENTS.md before starting any task.
> Do not make design decisions. Flag ambiguities instead of inferring intent.

---

## C9 — Dashboard: Submit Feature Page

**File:** `pipeline/dashboard/pages/3_Submit_Feature.py`

**What to build:**
A Streamlit page that lets a user paste a markdown feature spec and submit it to the pipeline.

**Spec:**
- `st.title("Submit Feature")`
- `st.text_area("Feature Specification (markdown)", height=300)` — multi-line input
- A Submit button
- On submit: POST the text to `http://api:8000/ingest/feature/markdown` with header `Content-Type: text/plain`
- Handle three response cases:
  - HTTP 200: show `st.success(f"Job created: {response.json()['job_id']}")` and a link to the Jobs page
  - HTTP 409: show `st.warning("This feature has already been submitted.")` (duplicate deduplication)
  - Any other error: show `st.error(f"Error {response.status_code}: {response.text}")`
- Use `requests` for HTTP (already in requirements.txt)
- The API base URL should come from an env var `API_URL` with default `http://api:8000`

**Do not:**
- Add authentication
- Add file upload (text area only for v1)
- Modify any other dashboard files

---

## C10 — Smoke Test Script

**File:** `pipeline/scripts/smoke_test.py`

**What to build:**
A CLI script that sends each fixture file to the ingest endpoint and prints the result.

**Spec:**
```
Usage: python pipeline/scripts/smoke_test.py [--base-url http://localhost:8000]
```
- Walk `pipeline/tests/fixtures/` for all `.md` files
- For each fixture: read the file, POST to `{base_url}/ingest/feature/markdown` with `Content-Type: text/plain`
- Print result per fixture:
  - Success: `[OK] feat-001-users-api.md → job_id=<id>`
  - Duplicate: `[SKIP] feat-001-users-api.md → already submitted`
  - Error: `[FAIL] feat-001-users-api.md → HTTP 500: <body>`
- Exit code 0 if all succeed or skip, exit code 1 if any fail
- Use `argparse` for the `--base-url` flag
- Use `requests` (already in requirements.txt)

**Do not:**
- Add polling or job status checking (that's an integration test)
- Add async code (plain sync requests is fine)

---

## C11 — Unit Tests for Markdown Parser

**File:** `pipeline/tests/test_ingest_parse.py`

**What to build:**
Unit tests for the `_parse_markdown` function in `api/routes/ingest.py`.

**Spec:**
- Import `_parse_markdown` from `api.routes.ingest`
- Write one test function per fixture file, using `pytest`
- For each test, read the fixture from `pipeline/tests/fixtures/`, call `_parse_markdown(text)`, assert on:
  - `feature_id` matches expected (e.g. `"FEAT-001"`)
  - `title` is non-empty
  - `description` is non-empty
  - `acceptance_criteria` is non-empty
  - `tech_stack_hint` is a non-empty list
- Also write one negative test: a markdown string with no recognized `# FEAT-` header returns a `FeatureSpec` with `feature_id == "unknown"` or raises `ValueError` — check what the current implementation does first before deciding which to assert

**Do not:**
- Mock the Redis deduplication layer (test `_parse_markdown` only, not the full route)
- Import FastAPI app or start a server

---

---

## C12 — NodeTrace Model + execution_trace on JobState

**Files:** `pipeline/models/job.py`, `pipeline/models/skill.py`

**What to build:**
Add the `NodeTrace` model and wire it into `JobState`.

**Spec — add to `models/job.py`:**
```python
class NodeTrace(BaseModel):
    node: str
    status_after: JobStatus
    provider: str | None = None
    duration_ms: int
    iteration: int
    error: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
```

Add to `JobState` (after `paused_at_node`):
```python
execution_trace: list[NodeTrace] = Field(default_factory=list)
```

**Do not:**
- Add `NodeTrace` to `models/skill.py` — it belongs in `models/job.py`
- Add any logic, methods, or validators — pure data model only
- Change any existing field names or types

---

## C13 — Trace Endpoint

**File:** `pipeline/api/routes/jobs.py`

**What to build:**
Add a read-only endpoint that returns the `execution_trace` for a job.

**Spec:**
```
GET /jobs/{job_id}/trace
```
- Load `JobState` from Redis key `job:{job_id}` (same pattern as existing `get_job`)
- Return 404 if not found
- Return `state.execution_trace` as a JSON array
- Response model: `list[NodeTrace]`
- Import `NodeTrace` from `models.job`

**Do not:**
- Change the existing `get_job` or `list_jobs` endpoints
- Add filtering or pagination

---

## C14 — JSON Logging Config

**File:** `pipeline/logging_config.py`

**What to build:**
A module that configures Python's standard `logging` to emit JSON lines to stdout.
No new dependencies — use only the stdlib `logging` module.

**Spec:**
Export one function: `setup_logging(level: str = "INFO") -> None`

Each log line must be a single JSON object with these keys:
- `ts` — ISO-8601 UTC timestamp (e.g. `"2026-04-19T12:00:00.000Z"`)
- `level` — `"INFO"`, `"WARNING"`, `"ERROR"`, etc.
- `logger` — the logger name (e.g. `"core.nodes.codegen"`)
- `msg` — the log message string
- `job_id` — if present in `extra` kwargs, include it; otherwise omit
- `node` — if present in `extra` kwargs, include it; otherwise omit

Implementation approach:
- Subclass `logging.Formatter`, override `format()` to build the dict and call `json.dumps()`
- In `setup_logging()`: call `logging.basicConfig()` with this formatter on a `StreamHandler`
- Set the root logger level from the `level` argument
- Silence noisy third-party loggers: set `httpx`, `httpcore`, `chromadb` to WARNING

**Do not:**
- Install `structlog`, `python-json-logger`, or any other package
- Modify any existing file
- Call `setup_logging()` from this module — it must be called explicitly by the app entry point

---

## C15 — Dashboard Trace Panel

**File:** `pipeline/dashboard/app.py`

**What to build:**
Add an execution trace expandable section to the existing Job Detail page.

**Spec:**
- Find the Job Detail section in `app.py` (the page that shows a single job's fields)
- After the existing status/error display, add:
  ```python
  if state.get("execution_trace"):
      with st.expander("Execution Trace", expanded=False):
          # render as a table
  ```
- Convert `execution_trace` list of dicts to a `pandas.DataFrame` with columns:
  `node`, `status_after`, `provider`, `duration_ms`, `iteration`, `error`, `timestamp`
- Display with `st.dataframe(df, use_container_width=True)`
- If `execution_trace` is empty or absent, show nothing (no empty expander)
- `pandas` is already a transitive dependency of Streamlit — no new imports needed in requirements.txt

**Do not:**
- Modify any other dashboard page
- Add polling or auto-refresh logic

---

## Notes for Codex

- C9–C15 are all independent and can be worked on in any order
- C12 modifies `models/job.py` (add model + field). C13 modifies `api/routes/jobs.py` (add endpoint). C14 and C15 create new files.
- If `_parse_markdown` is not exported from `api/routes/ingest.py`, make it importable (move to module level if it is currently nested inside a function)
- Do not touch `core/`, `DESIGN.md`, or `AGENTS.md`
- **C12 is a prerequisite for C13 and C15** — complete it first so the other tasks have the model available

---

## C16 — Dashboard: Admin Page

**File:** `pipeline/dashboard/pages/5_Admin.py`

**What to build:**
An admin page for triggering skill ingestion and watching progress live.

**Spec:**
```
API_URL = os.environ.get("API_URL", "http://api:8000")
```

Layout:
- `st.title("Admin")`
- `st.subheader("Skill Ingestion")`
- On page load: GET `{API_URL}/admin/ingest-skills/status` and display current state
  - Show `st.info("Status: idle")`, `st.success("Status: complete — N skills indexed")`, `st.error(...)` for failed, or a progress bar if running
- A **"Run Ingestion"** button:
  - If state is `"running"`: disable the button and show `st.warning("Ingestion already running.")`
  - Otherwise: POST `{API_URL}/admin/ingest-skills`, show `st.info("Ingestion started...")`
- **Live progress while running**: use `st.empty()` + a `while True` polling loop with `time.sleep(2)` that GETs status and updates a `st.progress(skills_processed / skills_total)` bar and a `st.text(f"{skills_processed} / {skills_total} skills embedded")` label until state is no longer `"running"`, then show final result
- Show `started_at` and `completed_at` timestamps if present

**Do not:**
- Add authentication
- Modify any other dashboard files
- Call ChromaDB or Redis directly — go through the API only

---

### What Claude owns next session (do not implement)
- Wiring `NodeTrace` population into `_job_worker` in `api/main.py`
- Calling `setup_logging()` from `api/main.py` on startup
These require modifying Claude-owned files.
