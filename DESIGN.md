# Skillnet Pipeline — Design Document

> **Living Document.** Every architectural decision, rationale, and change must be recorded here.
> When a decision changes, do not delete the old entry — update the Decision Log at the bottom.
> Last updated: 2026-04-20

---

## 1. Project Overview

Skillnet Pipeline is a State-Driven Agent Workflow that automates the path from a Rally story to committed code in a repository. It is not a chatbot or a one-shot code generator — it is a stateful job system where an LLM-powered agent analyzes requirements, retrieves relevant skill patterns from a vector store, generates code, runs tests, iterates on failures, and commits when coverage is met.

The existing skil catalog serves as the retrieval-augmented knowledge base. Agents do not generate from scratch — they generate against a retrieved context of known-good patterns.

### Goals

- Ingest feature specifications and produce committed, tested code with minimal human intervention
- Support both **new services** (new GitHub repo) and **change requests** against existing repos
- Retrieve relevant skills semantically rather than by keyword
- Support iterative self-correction through a test-and-retry loop
- Provide full observability into every job's state, provider used, and failure points
- Support multiple LLM providers with graceful degradation
- Persist job history across container restarts so past runs are always inspectable
- Track features and jobs by **project** to measure progress across test cases and real workstreams

### Non-Goals (v1)

- Real-time Rally webhooks (polling only in v1)
- Docker-in-Docker sub-agent sandboxing (in-process subprocess in v1)
- Local LLM as a production fallback (experimental/low-priority)
- Multi-tenant or multi-user support
- Full system decomposition across multiple repos in a single job (v2 multi-agent; see Section 17)

---

## 2. Team & Roles

| Agent | Role | Owns |
|-------|------|------|
| Claude (Sonnet/Opus) | Senior Developer, Quality Gate | Architecture, LangGraph core, LLM routing, ChromaDB retrieval, prompt engineering |
| Codex | Junior Developer | Boilerplate, data models, API skeletons, config wrappers, Docker Compose |
| Qwen | Planning & Communication | Requirement framing, task decomposition — **not a quality source for implementation details** |
| Human | Product Owner | Decisions, priorities, final approval |

---

## 3. Architecture Overview

```
Rally (Polling)
      │
      ▼
┌─────────────────────────────────────────────────────┐
│                  FastAPI Ingest Layer                │
│              POST /ingest/rally                      │
└─────────────────┬───────────────────────────────────┘
                  │ JobState created
                  ▼
┌─────────────────────────────────────────────────────┐
│           LangGraph Orchestrator (State Machine)     │
│                                                      │
│  INJECT → ANALYZE → RETRIEVE → CODE → TEST ──┐      │
│                                    ▲          │fail  │
│                                    └──────────┘      │
│                                         │pass        │
│                                       COMMIT         │
└─────────────────────────────────────────────────────┘
          │          │          │
          ▼          ▼          ▼
      ChromaDB     Redis     GitHub API
    (Skill Store) (Job State) (Repo Mgr)
                    │
                    ▼
           Streamlit Dashboard
            (Observability)
```

The orchestrator is a DAG managed by LangGraph. Every node receives a `JobState` object. State is checkpointed to Redis after every node transition using `langgraph-checkpoint-redis`.

---

## 4. Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Orchestration | LangGraph 0.2.x+ | Native stateful DAG, built-in checkpointing, conditional edge loops |
| Vector Store | ChromaDB (embedded) | Python-native, zero ops, persistent to local disk, first-class LangChain integration, free |
| Job State | Redis (`redis:alpine`) | In-memory speed, hash/set operations for queue and deduplication, no RediSearch needed |
| LLM Primary | Anthropic Claude (Sonnet/Opus) | Best code reasoning, highest quality ceiling |
| LLM Secondary | OpenAI GPT-4o | Strong fallback, same LangChain interface |
| LLM Experimental | LM Studio (OpenAI-compatible) | Local fallback, degraded quality, low priority |
| Embeddings | OpenAI `text-embedding-3-small` | Cost-effective, high quality, local fallback via LM Studio `nomic-embed-text` |
| API Layer | FastAPI | Async, Pydantic-native, minimal boilerplate |
| Dashboard | Streamlit | Python-native, rapid iteration, no frontend complexity |
| Repo Management | GitHub REST API via `requests` | Direct control, no third-party git lib required for v1 |
| Containerization | Docker Compose | Local dev orchestration, all services free and self-hosted |
| Config | YAML + Pydantic + env vars | Human-readable, validated at startup, secrets via env only |

---

## 5. Pipeline State Machine

### States

```
PENDING → INJECTED → ANALYZED → SKILLS_RETRIEVED → CODING → TESTING → COMMITTED
                                                              ↑           |
                                                              └── FAILED ─┘ (up to max_iterations)
                                                                      |
                                                                   EXHAUSTED (manual review)

ANY STATE → PAUSED (on node timeout or external signal)
```

### Node Definitions

| Node | LLM Tier | Responsibility |
|------|----------|---------------|
| `inject_node` | — | Validate and store Rally story, initialize JobState |
| `analyze_node` | HIGH | Parse story, identify tech stack, define success criteria |
| `retrieve_skills_node` | — | Semantic search ChromaDB, populate `skills_pool` |
| `codegen_node` | MEDIUM | Generate code using story + retrieved skills as context |
| `test_node` | — | Execute generated code, parse test output into structured result |
| `interpret_failure_node` | MEDIUM | Analyze test failures, update context for retry |
| `commit_node` | — | Push to GitHub, create branch, open PR |

### Iteration Loop

```
test_node
    │
    ├── PASS → commit_node
    │
    └── FAIL → interpret_failure_node → codegen_node (retry)
                     │
                     └── if iteration_count >= max_iterations → EXHAUSTED
```

`max_iterations` defaults to 3, configurable per job.

### Edge Cases & Alerts

| Scenario | Transition | Dashboard Signal |
|----------|-----------|-----------------|
| Test fails, retrying | `TESTING → FAILED → CODING` | ⚠️ Retry in progress (iteration N of max) |
| Max iterations hit | `FAILED → EXHAUSTED` | 🛑 Exhausted — manual review required |
| Repo creation fails | `INJECTED` stays, error logged | ✗ Repo creation failed — check GitHub token |
| Node timeout | Any state → `PAUSED` | 🕐 Stuck at [node] — awaiting intervention |
| Degraded output | Any state, `degraded=True` | ⚠️ Degraded — local LLM was used |

`PAUSED` is a terminal-but-resumable state. A paused job can be re-queued manually. Timeout threshold is configurable per node type (default: 5 minutes).

---

## 6. Data Models

### JobState (LangGraph State)

```python
class JobState(BaseModel):
    job_id: str
    story_id: str
    story_content: dict
    tech_stack: list[str]
    skills_pool: list[SkillMatch]
    repo_name: str
    repo_url: str | None                  # None = create new; pre-set = use existing (change request)
    pr_url: str | None                    # populated by commit_node after PR is opened
    generated_files: dict[str, str]       # path → content
    test_results: TestResult | None
    iteration_count: int = 0
    max_iterations: int = 3
    error_logs: list[str] = []
    status: JobStatus
    provider_log: list[str] = []          # which provider ran each node
    degraded: bool = False
    degraded_nodes: list[str] = []        # nodes that ran on local LLM
    repair_mode: bool = False             # if True, job was re-queued to fix degraded output
    last_commit_hash: str | None = None   # SHA of last successful commit, set by commit_node
    paused_at_node: str | None = None     # node name where job was paused, if applicable
    execution_trace: list[NodeTrace] = [] # per-node timing + outcome, populated by job worker
    # submission context (set at ingest time, read-only thereafter)
    job_type: str = "feature"             # "feature" | "new_service" | "change_request"
    project_id: str | None = None         # links job to a Project (see Section 21)
    parent_job_id: str | None = None      # set when job is a retry/resume of a prior job
    created_at: datetime
    updated_at: datetime
```

### Skill

```python
class Skill(BaseModel):
    skill_id: str                         # matches directory name in skills/
    name: str
    description: str
    category: str                         # from skills_catalog.json
    tags: list[str]
    body: str                             # markdown body of SKILL.md (frontmatter stripped)
    embedding: list[float]               # stored in ChromaDB
    source_path: str
    supports_codex: bool                  # from skills_index.json plugin.targets
    supports_claude: bool                 # from skills_index.json plugin.targets
```

### SkillMatch

```python
class SkillMatch(BaseModel):
    skill: Skill
    score: float                          # cosine similarity score
```

### TestResult

```python
class TestResult(BaseModel):
    passed: bool
    pass_count: int
    fail_count: int
    failures: list[str]                   # human-readable failure messages
    raw_output: str
```

### NodeTrace

Appended to `JobState.execution_trace` by the job worker after every node completes.
The worker owns population — nodes do not write this themselves.

```python
class NodeTrace(BaseModel):
    node: str                    # "inject", "analyze", "codegen", etc.
    status_after: str            # JobState.status.value after this node ran
    provider: str | None         # e.g. "codegen:claude-sonnet"; None for no-LLM nodes
    duration_ms: int             # wall-clock time for this node in milliseconds
    iteration: int               # codegen/interpret cycle index (0 for first pass)
    error: str | None            # first new error_log entry this node produced, if any
    input_tokens: int = 0        # tokens consumed by the LLM call (0 for non-LLM nodes)
    output_tokens: int = 0       # tokens produced by the LLM call
    cost_usd: float = 0.0        # estimated USD cost based on provider pricing table
    timestamp: datetime          # UTC time this node completed
```

`JobState` gains one new field:

```python
execution_trace: list[NodeTrace] = []   # ordered list of per-node execution records
```

---

## 7. LLM Strategy

### Provider Tiers

```yaml
# config/llm.yaml
tiers:
  high:
    primary: anthropic/claude-opus-4-7
    fallbacks: [openai/gpt-4o]
  medium:
    primary: anthropic/claude-sonnet-4-6
    fallbacks: [openai/gpt-4o, openai/gpt-4o-mini]
  low:
    primary: openai/gpt-4o-mini
    fallbacks: []
```

### Degraded Mode (Experimental)

Local LLM via LM Studio is available as an experimental provider. It is **not wired into any default tier**. To enable:

```yaml
# config/llm.yaml — EXPERIMENTAL section
experimental:
  local:
    enabled: false                        # must explicitly opt in
    base_url: http://localhost:1234/v1
    model: qwen2.5-coder-7b
    degraded_marker: true                 # always stamps JobState.degraded
```

When a local model runs any node:
- `JobState.degraded` is set to `True`
- The node name is appended to `JobState.degraded_nodes`
- The dashboard surfaces a warning on these jobs

Degraded jobs support a **repair flow**: re-queue with `repair_mode=True`. The repair pass feeds the existing degraded output + original story to a `HIGH` tier model with a targeted correction prompt. This is cheaper than a cold start.

### Fallback Behavior

LangChain's `.with_fallbacks()` handles provider failures transparently. If a provider throws `RateLimitError`, `AuthenticationError`, or `APIConnectionError`, the chain drops to the next provider in the tier. The `provider_log` in `JobState` records which provider actually ran.

---

## 8. ChromaDB Collection Schema

### Collection Setup

```python
import chromadb

client = chromadb.PersistentClient(path="./chroma_data")

skills_collection = client.get_or_create_collection(
    name="skills",
    metadata={"hnsw:space": "cosine"},
)
```

ChromaDB stores documents, embeddings, and metadata together. No separate schema definition required.

### Document Structure

```python
skills_collection.upsert(
    ids=[skill.skill_id],
    embeddings=[skill.embedding],
    documents=[skill.body],
    metadatas=[{
        "name": skill.name,
        "description": skill.description,
        "tags": ",".join(skill.tags),
        "source_path": skill.source_path,
    }],
)
```

### Skill Retrieval Query

```python
results = skills_collection.query(
    query_embeddings=[query_embedding],
    n_results=10,
    include=["documents", "metadatas", "distances"],
)
```

Distance is cosine — lower is more similar. Scores are inverted to `1 - distance` for consistency with the `SkillMatch.score` field (higher = better match).

---

## 9. Skill Ingestion Pipeline

### Source Files (all at `skillnet/` root)

| File | Purpose |
|------|---------|
| `skills/*/SKILL.md` | Full skill content — body text for embeddings |
| `skills_catalog.json` | Structured metadata for all 1,431 skills (id, name, description, category, tags, triggers, path) |
| `skills_index.json` | Extended index with plugin metadata (codex/claude target support flags) |
| `skills_bundles.json` | Named skill groupings (e.g. "core-dev") — useful for batch ingestion |
| `skills_aliases.json` | Short-name → canonical skill ID mappings |
| `skills_workflows.json` | Multi-skill workflow templates — future use |

### Ingestion Steps

1. Read `skills_catalog.json` for structured metadata (faster than walking + parsing all SKILL.md frontmatter)
2. For each skill entry, read the body from `skills/{id}/SKILL.md` (strip frontmatter, keep markdown body)
3. Generate embedding via OpenAI `text-embedding-3-small` on `description + "\n" + body[:500]`
4. Upsert into ChromaDB `skills` collection (idempotent — upsert by `skill_id`)
5. ChromaDB collection persists to `./chroma_data/` — survives restarts, mount as Docker volume

Ingestion script: `pipeline/scripts/ingest_skills.py`
Run manually or at container startup if ChromaDB collection is empty (`collection.count() == 0`).

---

## 10. Feature Ingestion

### v1: Markdown File (POC)

This project is a POC for an org that uses Rally. No personal Rally account is available during development. Input is a markdown file with a standardised structure. The pipeline is Rally-agnostic at this layer — the `FeatureSpec` model is the internal contract; the source of that data is pluggable.

**Accepted markdown format:**

```markdown
# FEAT-1234: Feature Title

## Description
Feature description text...

## Acceptance Criteria
- Criterion 1
- Criterion 2

## Tech Stack (optional hints)
- Python
- FastAPI
```

**Ingestion:** `POST /ingest/feature` accepts either:
- Raw markdown body (parsed server-side into `FeatureSpec`)
- Pre-parsed `FeatureSpec` JSON

**Deduplication:** store `feature_id` in Redis set `features:seen`.

### v2: Rally REST API (future)

When connecting to the org's Rally instance:
- `GET /rally/slm/webservice/v2.0/hierarchicalrequirement` filtered by status and modified date
- Auth: Rally API key via `ZSESSIONID` header
- Map Rally fields: `FormattedID` → `feature_id`, `Description` → `description`, `c_AcceptanceCriteria` → `acceptance_criteria`
- Poll interval: configurable, default 60 seconds

### FeatureSpec Model

Replaces the earlier `RallyStory` model throughout the codebase.

```python
class FeatureSpec(BaseModel):
    feature_id: str                          # e.g. "FEAT-1234"
    title: str
    description: str                         # raw markdown
    acceptance_criteria: str = ""            # raw markdown list
    tech_stack_hint: list[str] = Field(default_factory=list)
    source: str = "markdown"                 # "markdown" | "rally" | "manual"
    # submission context (v1.1+)
    job_type: str = "feature"               # "feature" | "new_service" | "change_request"
    target_repo: str | None = None          # existing repo name (change_request only)
    project_id: str | None = None           # links to a registered Project (see Section 21)
```

### Submission Modes

| `job_type` | `target_repo` | Behaviour |
|------------|--------------|-----------|
| `feature` | None | New repo created, named `skillnet-{feature_id}`. Current v1 default. |
| `new_service` | None | New repo created, named from `title` slug. Prompts for full service scaffold (entrypoint, config, Dockerfile, tests). |
| `change_request` | `"my-existing-repo"` | No repo created. `inject_node` fetches existing file tree from GitHub and adds it to `JobState.story_content["existing_files"]`. `codegen_node` receives this as read-only context. PR opened against existing repo's main branch. |

`inject_node` is responsible for resolving `job_type` into the correct `JobState` fields. `commit_node` already skips `create_repo()` when `repo_url` is pre-set — no graph changes required for `change_request` mode.

---

## 11. Repository Management

### Flow

1. `RepoManager.create_repo(name)` → GitHub API `POST /user/repos`
2. `RepoManager.create_branch(repo, branch)` → branch off `main`
3. `RepoManager.push_files(repo, branch, files, commit_msg)` → commit via GitHub Contents API (no local git required for small changesets)
4. `RepoManager.create_pr(repo, branch, title, body)` → open PR for review

### Auth

GitHub Personal Access Token via `GITHUB_TOKEN` env var. Token scopes required: `repo`, `workflow`.

---

## 12. Deployment (Local Dev)

```yaml
# docker-compose.yml services
redis:     redis:alpine                  # standard Redis, job state only — no RediSearch needed
api:       ./Dockerfile.api              # FastAPI + LangGraph + ChromaDB (embedded)
dashboard: ./Dockerfile.dashboard        # Streamlit
```

ChromaDB runs embedded inside the `api` container — no separate service. The `./chroma_data` directory is mounted as a volume so the skill index persists across container restarts.

Env vars in `.env` file (never committed):

```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GITHUB_TOKEN=
GITHUB_OWNER=                              # personal account username
REDIS_URL=redis://redis:6379
```

---

## 13. Observability Dashboard (Streamlit)

Pages:
- **Jobs Queue** — all active/completed jobs with status badges
- **Job Detail** — state snapshot, skills used, iteration history, provider log, error logs
- **Skill Pool** — browse and search the ChromaDB skill collection
- **Degraded Jobs** — filtered view of jobs with `degraded=True`, repair action button

---

## 14. Open Questions

| # | Question | Owner | Status |
|---|----------|-------|--------|
| 1 | Rally workspace URL and auth method | Human | **Resolved** — Rally deferred; v1 uses markdown ingestion |
| 2 | GitHub org vs personal account | Human | **Resolved** — personal account, `GITHUB_OWNER` env var |
| 3 | Max file size for GitHub Contents API vs requiring local git | Claude | Open |
| 4 | Test runner strategy — what test framework do generated projects use? | Claude | Open |
| 5 | v2: Linking a feature to an existing repo/project (`target_repo` in FeatureSpec) | Human | Deferred to v2 |

---

## 15. Node Context Architecture (Subagent Design)

### The Problem

Passing the full `JobState` to every LLM call is wasteful and degrades quality. A `codegen_node` that receives the entire job history, all error logs, all skill bodies, and the complete Rally story will produce worse output than one that receives only what it needs. Context bloat also drives up latency and cost.

### Principle: Context Pruning Per Node

Each LLM-facing node extracts a **minimal context slice** from `JobState`. The node function is responsible for this extraction before building its prompt. Nodes never pass raw `JobState` to the LLM.

| Node | Context slice passed to LLM | Target token budget |
|------|----------------------------|-------------------|
| `analyze_node` | Feature markdown only (description + acceptance_criteria) | ~2K |
| `codegen_node` | Feature summary + top 3 skill summaries (truncated) + file tree + target file spec | ~6–8K |
| `interpret_failure_node` | Failing test names + raw output + **only the files that caused failures** | ~4K |

Skill bodies are **never passed in full** to `codegen_node`. Each retrieved skill contributes at most its `description` + first 300 chars of body as a pattern hint. The full body lives in ChromaDB for retrieval only.

### Timeout Strategy

Each LLM node is wrapped with an async timeout. If the timeout is exceeded:
- `JobState.status` → `PAUSED`
- `JobState.paused_at_node` → node name
- Job is written back to Redis and removed from the active queue
- Dashboard surfaces the stuck node with a manual re-queue action

Default timeouts by node tier:

| Tier | Default timeout |
|------|----------------|
| HIGH (analyze) | 90 seconds |
| MEDIUM (codegen, interpret) | 120 seconds |
| No LLM (test, inject, retrieve) | 30 seconds |

### v2: Parallel Node Execution

LangGraph's `Send` API enables fan-out. Once `analyze_node` completes, the graph can dispatch `retrieve_skills_node` and `plan_file_tree_node` simultaneously rather than sequentially. This cuts wall-clock time on the ANALYZE → CODE path roughly in half.

This is deferred to v2. v1 uses a sequential graph with per-node context pruning and timeouts.

---

## 16. Admin Operations

### Skill Ingestion

Skills must be embedded and indexed into ChromaDB before the retrieval node can function.
This is a one-time setup step, and a re-index step whenever the skill catalog changes.

**Flow:**
1. `POST /admin/ingest-skills` — triggers background ingestion, returns 202. Returns 409 if already running.
2. Background task calls `load_all_skills()` (loads from `skills_catalog.json` + `skills/*.md`) then `SkillStore.embed_and_upsert()` in batches of 100, updating in-memory `IngestionStatus` after each batch.
3. `GET /admin/ingest-skills/status` — returns `{state, skills_processed, skills_total, started_at, completed_at, error}`.

**State values:** `idle` → `running` → `complete` | `failed`

**Design decisions:**
- `IngestionStatus` is an in-memory module-level singleton in `api/routes/admin.py`. Not persisted to Redis. Acceptable for a single-instance POC — re-index state is ephemeral and resets on restart.
- Only one ingestion at a time enforced by 409 check.
- `SkillStore.embed_and_upsert()` accepts an optional `on_batch(processed, total)` callback for progress reporting without coupling progress logic to the store.
- Admin endpoints have no auth in v1 — internal tooling only.

**Files:**
- `api/routes/admin.py` — trigger endpoint, status endpoint, background task, IngestionStatus
- `core/skills.py` — `embed_and_upsert(on_batch=...)` callback added
- `dashboard/pages/5_Admin.py` — trigger button + live progress bar (polls `/admin/ingest-skills/status`)

---

## 17. v2 Pipeline Architecture — Multi-Agent Decomposition

> This section captures the full product vision for the pipeline. v1 validates the end-to-end
> path with a single codegen agent. v2 implements this architecture once v1 is proven.

### Vision

Every feature submitted to the pipeline must produce output that:
1. **Works** — all generated tests pass before any commit is made
2. **Has tests** — unit test coverage is a non-negotiable output artifact, not optional
3. **Is extensible** — generated code must support future work items being submitted against the same repo without requiring rewrites
4. **Is complete** — no stubs, no TODOs, no placeholder implementations

### Complexity Assessment

After `analyze_node` runs, a new **complexity classifier** determines whether the feature
can be handled by a single codegen agent or must be decomposed.

```
Simple  → single agent path (current v1 flow)
Complex → decompose → fan-out → merge → validate
```

Complexity signals (heuristics, not hard rules):
- `file_plan` has more than 4 files
- `acceptance_criteria` contains more than 5 distinct assertions
- `tech_stack` spans more than 2 layers (e.g., API + persistence + auth)
- `summary` contains words like "integrate", "middleware", "pipeline", "authentication"

### Decompose Node

For complex features, a `decompose_node` runs after `analyze_node`. It produces:

```python
class SubTask(BaseModel):
    id: str                    # e.g. "impl", "tests", "docs"
    description: str           # what this agent is responsible for
    files: list[str]           # subset of file_plan this agent owns
    interface_contract: str    # binding API surface this agent must respect
                               # e.g. class signatures, function names, return types
    depends_on: list[str]      # other subtask IDs this one must not conflict with

class DecomposeResult(BaseModel):
    subtasks: list[SubTask]
    shared_contract: str       # full interface spec shared across ALL subtasks
                               # e.g. "RateLimiter(limit: int, window: int) -> None"
                               #      "allow(key: str) -> bool"
```

The `shared_contract` is the critical output. Every downstream agent receives it as a
hard constraint. Without it, parallel agents produce conflicting implementations.

**Standard decomposition pattern:**

| Sub-task ID | Responsibility | Owns |
|-------------|---------------|------|
| `core` | Core implementation logic | Main module files |
| `tests` | Unit test suite | `tests/test_*.py` |
| `docs` | Docstrings + README | Inline docs, `README.md` |

For larger features:
| `api` | Public interface / entry points | Route handlers, CLI |
| `data` | Data models and storage | Models, schemas |
| `core` | Business logic | Service layer |
| `tests` | Full test suite | All test files |

### Fan-Out via LangGraph Send API

```python
from langgraph.types import Send

def route_subtasks(state: JobState) -> list[Send]:
    return [
        Send("codegen_subtask", {**state.model_dump(), "active_subtask": st})
        for st in state.subtasks
    ]
```

Each `codegen_subtask` node receives:
- Its specific `SubTask` (files it owns, its description)
- The `shared_contract` as a hard constraint in the system prompt
- The retrieved skill context (same pool, each agent uses what's relevant)
- NO other subtask's output — agents are fully independent

Results are written back into `JobState.generated_files` keyed by file path.
Since each subtask owns distinct files, merge is a simple dict union with conflict detection.

### Merge and Validate Node

After all subtask codegen nodes complete, a `merge_node`:
1. Assembles `generated_files` from all subtask outputs
2. Detects file ownership conflicts (same path written by two agents → flag + retry decompose)
3. Runs a lightweight static check: do imports reference files that exist in the output set?
4. Hands off to `test_node` — same as v1

`test_node` and the interpret/retry loop remain unchanged. The only difference is the
input to `test_node` is now a merged multi-agent output instead of a single-agent output.

### Quality Gates (non-negotiable)

These apply to ALL jobs regardless of simple vs. complex path:

```
[ ] All tests generated and present in output
[ ] All tests pass (test_node returns passed=True)
[ ] No files contain the string "TODO", "pass", "raise NotImplementedError", or "..."
    as the sole body of a function (checked in merge_node)
[ ] Iteration limit not exhausted (EXHAUSTED status = human review required)
[ ] Commit only happens after all gates pass
```

The `commit_node` must check these gates explicitly before calling `repo_manager`.
A job that reaches `commit_node` with failing tests is a pipeline bug, not a feature.

### "Supports Additional Work Items" Constraint

The `analyze_node` prompt gains a new requirement:

> The generated code must follow patterns that allow future features to build on it:
> - No hardcoded values that belong in config
> - No global mutable state outside of explicitly designated stores
> - Public interfaces must be stable (don't bury behavior in private functions if it will be extended)
> - Follow the single-responsibility principle at the module level

The `shared_contract` output from `decompose_node` becomes the stable API surface that
future work items reference when they target the same repo.

### v2 State Changes

`JobState` gains:

```python
subtasks: list[SubTask] = Field(default_factory=list)
active_subtask: SubTask | None = None        # set per-agent during fan-out
shared_contract: str = ""                    # binding interface from decompose_node
complexity: str = "simple"                  # "simple" | "complex"
quality_gates_passed: bool = False           # set by merge_node before commit
```

### Updated Pipeline Graph (v2)

```
inject → analyze → [complexity check]
                        │
              ┌─────────┴──────────┐
           simple              complex
              │                    │
           retrieve           decompose
              │                    │
           codegen           retrieve (shared)
              │                    │
           test              fan-out (Send API)
              │              codegen_subtask ×N
           interpret ←────── merge_node
              │                    │
           commit ←────────── test_node
                                   │
                              interpret (shared)
                                   │
                              commit
```

---

## 18. v2 Other Deferred Work

| Item | Description | Design Notes |
|------|-------------|--------------|
| Rally v2 ingestion | Parser that produces `FeatureSpec` from Rally stories. Internal contract is already stable. | Add `api/routes/rally.py` |
| Docker-in-Docker test sandboxing | Run generated code in isolated container. Prevents access to host env/filesystem. | subprocess + tempdir sufficient for v1 |
| Per-file codegen (single-agent) | Run `codegen_node` once per file in `file_plan`. Better quality at cost of N LLM calls. | Partially superseded by multi-agent decomposition |
| Existing codebase context window | For `change_request` jobs, fetch full file tree + content from GitHub and inject into codegen context. Needs chunking strategy for large repos. | v1.1 task; max 20 files, truncate at 3KB each |
| Complexity auto-classifier | After `analyze_node`, route simple vs complex to single-agent vs multi-agent path. | Heuristics defined in Section 17; not wired in v1 |
| HyDE retrieval | Before querying ChromaDB, make a small LLM call to generate a synthetic skill description from the analysis summary. Embed that instead of the raw feature text. Closes the semantic gap between feature-spec language and skill-body language. ~$0.001/job. | Currently: use analyze_node summary directly (good); HyDE would be marginally better at higher precision |
| Skill catalog expansion | Add common CS pattern skills (concurrency, caching, rate limiting, retry, circuit breaker) to close the gap for non-AI features. Current catalog is heavily AI/LLM-weighted. | Manual curation task; not a code change |

---

## 19. Job Persistence & History

### Problem

Redis by default stores data only in memory. A `docker compose down` or volume wipe clears all job state. After a container rebuild, the Jobs Queue page shows nothing. For a dev tool that runs expensive LLM jobs, losing run history is unacceptable.

### Solution: Redis Persistence Volume

Configure Redis with append-only persistence and mount a named volume:

```yaml
# docker-compose.yml
redis:
  image: redis:alpine
  command: redis-server --appendonly yes --appendfsync everysec
  volumes:
    - redis-data:/data

volumes:
  redis-data:
```

`appendonly yes` writes every command to an AOF log on disk. `appendfsync everysec` flushes once per second — good balance between durability and performance. The named volume `redis-data` survives `docker compose down` and `docker compose up` cycles. It is only destroyed by `docker compose down -v` (explicit volume wipe).

**What persists:**
- All `job:{id}` hashes
- `jobs:all` set (complete job registry)
- `features:seen` set (deduplication)
- Redis checkpointer state (LangGraph thread snapshots)

**What does not persist:**
- `IngestionStatus` singleton in `admin.py` (in-memory, intentional — re-run ingestion after restart)
- `jobs:queue` list — incomplete jobs (PENDING) from before restart will reappear in the queue on next startup

### Historical Runs Dashboard

The Jobs Queue page (`1_Jobs_Queue.py`) currently shows only the `jobs:all` set via `GET /jobs`. With persistence, this naturally becomes a historical view.

**Additions needed (v1.1):**

- `GET /jobs` gains query params: `status=committed`, `project_id=PROJ-001`, `limit=50`, `offset=0`
- Jobs Queue page gains a **filter sidebar**: status filter, project filter, date range
- Job cards show `created_at`, elapsed time, and cost summary at a glance
- A dedicated **History** page (`6_History.py`) shows an aggregate table: total jobs run, success rate, total tokens used, total estimated cost, breakdown by project

### Data Retention

No automatic TTL on job keys in v1. All jobs kept indefinitely. If the volume grows large, manual cleanup via `GET /jobs?status=committed` + `DELETE /jobs/{id}` (v1.1 admin endpoint).

---

## 20. Submission Modes: New Service vs. Change Request

### Overview

Three submission modes are supported. The caller sets `job_type` in `FeatureSpec`. The ingest layer and `inject_node` handle the branching — the LangGraph graph itself is unchanged.

| Mode | When to use | Repo outcome |
|------|-------------|-------------|
| `feature` | Isolated utility, library, or module. No strong service identity. | New repo named `skillnet-{feature_id}` |
| `new_service` | Full runnable service with entry point, config, Dockerfile. | New repo named from title slug |
| `change_request` | Adding to or modifying an existing codebase. | Existing repo; new branch + PR |

### New Service Mode

`new_service` differs from `feature` in the **system prompt** given to `analyze_node`:

> The output must be a complete, runnable service. In addition to the feature files, always include:
> - An entry point (`main.py` or equivalent)
> - A configuration module (`config.py` or `settings.py`)
> - A `Dockerfile` appropriate for the language/framework
> - A `README.md` with setup and run instructions
> - A `requirements.txt` / `pyproject.toml` / `package.json` as appropriate

The `file_plan` from `analyze_node` will include these scaffolding files. `codegen_node` generates them alongside the feature code.

`inject_node` routes this by injecting a `job_type` hint into `story_content["job_type"]` which `analyze_node` reads when building its prompt.

### Change Request Mode

When `job_type = "change_request"` and `target_repo` is set:

1. **`inject_node`** calls `repo_manager.get_file_tree(target_repo)` — lists all files in the existing repo (max depth 3, max 50 files).
2. The file tree (paths only, no content) is stored as `story_content["existing_files"]`.
3. **`analyze_node`** receives the existing file list as additional context in its prompt:
   > The following files already exist in the target repository. Your file plan must only add or modify files — never recreate files that should remain unchanged.
4. **`codegen_node`** fetches content for files it plans to modify (up to 3KB each, via GitHub Contents API) and adds them as `# EXISTING FILE — modify as needed` headers.
5. **`commit_node`** already handles updates correctly: `push_files` checks for existing file SHA before PUT, so it issues a modify commit rather than a create.

**RepoManager additions needed:**
```python
def get_file_tree(self, repo: str, path: str = "", depth: int = 3) -> list[str]:
    """Return list of file paths in the repo up to the given depth."""

def get_file_content(self, repo: str, path: str, ref: str = "main") -> str:
    """Return decoded file content for a single path."""
```

### Dashboard Submit Form

`pages/3_Submit_Feature.py` gains:

```
Job Type:  [Feature ▼]   (Feature / New Service / Change Request)
Project:   [None ▼]      (populated from GET /projects)
Target Repo: [          ] (shown only when Change Request selected)
```

The form POSTs to `POST /ingest/feature/markdown` with the extra fields in the request body. The API's `_parse_markdown` function already supports extension — `FeatureSpec` fields for `job_type`, `target_repo`, and `project_id` are added to the model and populated from the form, not the markdown content.

---

## 21. Project Registry

### Purpose

A **Project** is a named collection of related features and jobs. It answers the question: "What have we built for this workstream, and how did those runs go?"

Use cases:
- Group the rate-limiter, circuit-breaker, and retry features under a `"resilience-utils"` project
- Track which test fixtures (`feat-001` through `feat-010`) have been successfully committed
- See total spend and success rate per project

### Data Model

```python
class Project(BaseModel):
    project_id: str                        # e.g. "PROJ-001" or slug "resilience-utils"
    name: str
    description: str = ""
    repo_url: str | None = None            # primary GitHub repo, if this is a multi-feature project
    feature_ids: list[str] = []            # feature_ids submitted under this project
    created_at: datetime
    updated_at: datetime
```

Projects are stored in Redis as `project:{project_id}` (JSON hash). The set `projects:all` holds all project IDs (same pattern as jobs).

### API Endpoints

```
POST   /projects                   → create project, returns {project_id}
GET    /projects                   → list all projects (id, name, description)
GET    /projects/{project_id}      → full project detail
GET    /projects/{project_id}/jobs → all jobs linked to this project, with status summary
```

### Linking Features to Projects

At submission time, set `project_id` in `FeatureSpec`. `inject_node` writes the `feature_id` into `project:{project_id}:features` (Redis set) and the `job_id` into `project:{project_id}:jobs`.

If `project_id` is omitted, the job is not linked to any project. Retroactive linking is out of scope for v1.

### Dashboard: Projects Page

New page `pages/6_Projects.py`:

- List of projects with: name, feature count, job count, success rate (committed / total), total cost
- Click a project → drill-down: list of jobs with status badges and elapsed time
- "New Project" form (name + description)
- Each job row links to Job Detail page

### Test Case Tracking

The project registry is the mechanism for tracking test cases. For the POC test suite, register one project per scenario:

| project_id | name | Purpose |
|------------|------|---------|
| `test-utilities` | Utility Functions | Rate limiter, circuit breaker, token bucket |
| `test-services` | New Services | Full service scaffold test cases |
| `test-changes` | Change Requests | Modify-existing-repo test cases |

Every test fixture (`feat-001.md` through `feat-N.md`) is submitted with the appropriate `project_id`. The Projects page then shows exactly which fixtures have been run and which passed.

---

## 22. Failure Recovery & Iteration

### Problem

A job may reach `FAILED` or `EXHAUSTED` state after using up its iteration budget. Today, the only option is to submit a new job from scratch — losing all prior context, token spend, and generated artifacts.

Three recovery operations are needed:

### Operation 1: Resume (PAUSED jobs)

A `PAUSED` job timed out at a node. The pipeline state is fully intact in Redis. Re-queuing restarts from the beginning of that node, not from scratch.

```
POST /jobs/{job_id}/resume
```

- Validates `status == "paused"`
- Resets `status → PENDING`, clears `paused_at_node`
- Pushes `job_id` back onto `jobs:queue`
- The LangGraph checkpointer holds the full thread state — the graph resumes from where it paused

No changes to `JobState` or the graph are required. This operation already works today because LangGraph's checkpointer stores intermediate state. The only missing piece is the API endpoint and a Resume button on the Job Detail page.

### Operation 2: Retry (FAILED / EXHAUSTED jobs)

A job exhausted its iteration budget. The last `generated_files` exist and the `error_logs` are populated.

```
POST /jobs/{job_id}/retry
  Body: { "max_iterations": 5 }  # optional override
```

Creates a **new** `JobState` that:
- Copies `story_content`, `tech_stack`, `skills_pool` from the failed job
- Copies `generated_files` (start from last attempt, not blank slate)
- Copies all `error_logs` (full failure context is injected into `codegen_node`'s fix prompt)
- Sets `parent_job_id = job_id` (links new job to its origin)
- Sets `iteration_count = 0`, `max_iterations` from body (default: original + 2)
- Sets `status = PENDING`, pushes to queue

The new job skips `analyze_node` and `retrieve_skills_node` (story is already analyzed, skills already retrieved) and jumps directly into `codegen_node` with the existing files + error context. This is implemented by adding a `resume_from` edge in the graph that bypasses the first two nodes when `parent_job_id` is set and `generated_files` is non-empty.

**`codegen_node` behavior when `parent_job_id` is set:**
The fix prompt already includes `error_logs` when `iteration_count > 0`. Since the retry job starts with the full `error_logs` from the prior run, no changes to the codegen prompt are needed — the fix context is already there.

### Operation 3: Patch Retry (manual correction hint)

The user has reviewed the failed output and wants to add a specific fix instruction before re-running.

```
POST /jobs/{job_id}/retry
  Body: {
    "patch_instructions": "The token refill calculation is wrong. Use window_seconds, not rate.",
    "max_iterations": 3
  }
```

Same as Operation 2, but `patch_instructions` is prepended to `story_content["patch_context"]`. `codegen_node` injects this at the top of the fix prompt:

```
MANUAL FIX INSTRUCTIONS (apply first, before analyzing error logs):
{patch_instructions}
```

### Dashboard Integration

**Job Detail page additions:**

- **Resume button**: shown when `status == "paused"`. POSTs to `/jobs/{id}/resume`.
- **Retry button**: shown when `status in ("failed", "exhausted")`. Opens a modal with optional `max_iterations` and `patch_instructions` fields. POSTs to `/jobs/{id}/retry`.
- **Parent job link**: when `parent_job_id` is set, show "Retry of [parent_id]" with a link to the parent job.
- **Child job link**: conversely, if a job has spawned a retry, show "Retried as [child_id]".

### Iteration Chain Visibility

In the Jobs Queue page, jobs with `parent_job_id` are grouped with their parent under a collapsible row. This makes it easy to see "FEAT-004 has been attempted 3 times — the third attempt committed."

### API Endpoints Summary

```
POST /jobs/{job_id}/resume          → re-queue a PAUSED job
POST /jobs/{job_id}/retry           → create a new job from a FAILED/EXHAUSTED job's state
GET  /jobs/{job_id}/children        → list jobs that were created as retries of this job
```

---

## 23. Decision Log

All significant decisions are recorded here with rationale. Do not delete entries.

| Date | Decision | Rationale | Alternatives Considered |
|------|----------|-----------|------------------------|
| 2026-04-19 | LangGraph as orchestrator | Best stateful DAG with native checkpointing and conditional loops | Prefect, Airflow (too heavy), raw threading (no state management) |
| 2026-04-19 | Redis Stack for both VSS and state | Single service for vector search + job state cache reduces infrastructure | Pinecone (external, latency), Chroma (no built-in job state) |
| 2026-04-19 | **REVISED** — ChromaDB (embedded) for vector store, plain Redis for job state | Redis Stack was overkill; RediSearch adds ops complexity and a DSL with no Python benefit. ChromaDB is Python-native, zero ops, free, and has first-class LangChain integration. Plain `redis:alpine` handles job state with no added modules. | LanceDB (newer, less stable ecosystem for a project already managing stale references) |
| 2026-04-19 | Rally integration via polling not webhooks | Webhooks require public endpoint; polling is viable for v1 cadence | Webhooks deferred to v2 |
| 2026-04-19 | Sub-agent sandboxing in-process for v1 | Docker-in-Docker adds complexity without v1 value; subprocess + temp dir is sufficient | Docker-in-Docker deferred to v2 |
| 2026-04-19 | Local LLM as experimental/degraded only | Local model quality is noticeably worse; better to mark and repair than silently degrade | Dropped local from critical path entirely; kept as opt-in with degraded marker |
| 2026-04-19 | Degraded repair flow instead of re-run | Feeding degraded output to a high-tier model for correction is cheaper than cold start | Full re-run from INJECT state |
| 2026-04-19 | GitHub Contents API for file commits | Avoids local git dependency for small changesets | gitpython (heavier dep), subprocess git (brittle) |
| 2026-04-19 | Kept `interpret_failure_node` as single node (not split into DEBUG + FIX) | Qwen proposed three separate nodes (ANALYZE_ERROR → DEBUG → FIX). Single node is sufficient for v1; splitting adds graph complexity with minimal observability gain at this scale. Revisit in v2 if failure analysis proves insufficient. | Three-node debug chain |
| 2026-04-19 | Added `PAUSED` state and `last_commit_hash` / `paused_at_node` fields | Adopted from Qwen's edge case review. `PAUSED` handles node timeouts. `last_commit_hash` enables resume and audit. | Ignoring timeout state entirely |
| 2026-04-19 | Rally integration replaced with markdown file ingestion for v1 | No personal Rally account available; project is a POC. Markdown is a clean abstraction layer — Rally can be wired in v2 by adding a parser that produces `FeatureSpec`. | Building Rally mock/stub |
| 2026-04-19 | GitHub personal account via `GITHUB_OWNER` env var | Confirmed by product owner. Org account deferred until POC is validated. | GitHub org with fine-grained tokens |
| 2026-04-19 | Per-node context pruning instead of passing full JobState to LLM | Reduces token cost, improves LLM focus, lowers latency. Adopted as core principle for all node implementations. | Passing full state and relying on LLM to ignore irrelevant fields |
| 2026-04-19 | Async timeout wrapper per node, PAUSED state on timeout | Avoids silent hangs. Per-tier thresholds: 90s HIGH, 120s MEDIUM, 30s no-LLM. v2 adds parallel fan-out via LangGraph Send API. | Global pipeline timeout only |
| 2026-04-19 | Adopted Qwen Q1a/Q1c prompt styles; rejected Q1b single-file-per-call design | Q1a and Q1c improved clarity and added explicit `{failing_tests}` variable. Q1b proposed one LLM call per file — contradicts `CodegenResult.files: dict[str,str]` design and would multiply LLM cost by N files. Multi-file batch generation is maintained. | Per-file generation loop |
| 2026-04-20 | v2 multi-agent decomposition architecture documented | Vision: complex features decomposed into independent sub-agents (core/tests/docs) via LangGraph Send API, bound by a shared interface contract from decompose_node. Quality gates enforced before commit. v1 validates the single-agent path first. | Single-agent with per-file calls (Qwen Q1b approach — superseded by this design) |
| 2026-04-20 | Skill ingestion exposed as admin API endpoint with live progress | Running ingestion as a terminal script gave no observability. Admin endpoint + dashboard page lets operator trigger re-index and monitor progress without terminal access. In-memory status is acceptable for single-instance POC. | Cron-scheduled auto-ingestion (deferred — manual trigger sufficient for v1) |
| 2026-04-19 | Added NodeTrace model + execution_trace to JobState | Per-node timing, provider, and error record needed for actionable debugging. Job worker owns population (already sees every node chunk with timing context) — nodes stay clean. Gives Claude, Codex, and human a single ordered list to diagnose slow/failing jobs. | Structured log-only approach (no queryable model); per-node self-reporting (adds coupling to every node) |
| 2026-04-19 | Linking features to existing repos deferred to v2 | v1 always creates a new GitHub repo. v2 will add optional `target_repo` field to `FeatureSpec`; `inject_node` would populate `JobState.repo_url` from it, bypassing `create_repo` in `commit_node`. No schema change required today — `commit_node` already skips creation when `repo_url` is set. | Adding `target_repo` to v1 FeatureSpec |
| 2026-04-20 | Job history persisted via Redis AOF + named volume | Jobs in Redis are lost on container restart without a volume. `appendonly yes` + `redis-data` named volume makes all job state durable across `docker compose down/up`. Only `docker compose down -v` destroys history. | Postgres for job history (too heavy for a POC); SQLite (adds dependency) |
| 2026-04-20 | Three submission modes: feature / new_service / change_request | Product requirement: system must handle new repos, full service scaffolds, and changes to existing repos. `job_type` field in `FeatureSpec` routes the behaviour at ingest time. Graph is unchanged — routing handled in `inject_node`. | Separate API endpoints per mode (fragmented; harder to extend) |
| 2026-04-20 | Project registry in Redis, same pattern as job storage | Features need to be grouped for test case tracking and workstream visibility. Redis set `projects:all` + hash `project:{id}` is consistent with existing job storage pattern. No new infrastructure needed. | SQLite project table (adds dependency); in-memory dict (not persistent) |
| 2026-04-20 | Three-tier failure recovery: resume / retry / patch-retry | PAUSED jobs can resume from LangGraph checkpoint. FAILED/EXHAUSTED jobs need a new job seeded with prior context. User-supplied patch instructions are a third tier that accelerates convergence without a full re-run. All three use `parent_job_id` linkage for traceability. | Single "re-run from scratch" operation only |
| 2026-04-20 | `pr_url` added to JobState; returned by commit_node | PR URL was logged but not stored. Storing it in state makes it accessible to the dashboard, the Project registry, and future Rally integration without a GitHub API roundtrip. | Fetch PR URL on demand via GitHub API from branch name |
| 2026-04-20 | Token usage and cost tracked per NodeTrace entry | Per-node token counts (via LangChain `TokenUsageHandler` callback) give actionable cost attribution. Summing across the trace gives job-level totals for the dashboard metrics row. Cost estimated from provider label using Anthropic public pricing table. | Job-level totals only (masks which nodes are expensive); no cost tracking (can't optimize spend) |
