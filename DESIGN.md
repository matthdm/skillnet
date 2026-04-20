# Skillnet Pipeline — Design Document

> **Living Document.** Every architectural decision, rationale, and change must be recorded here.
> When a decision changes, do not delete the old entry — update the Decision Log at the bottom.
> Last updated: 2026-04-19

---

## 1. Project Overview

Skillnet Pipeline is a State-Driven Agent Workflow that automates the path from a Rally story to committed code in a repository. It is not a chatbot or a one-shot code generator — it is a stateful job system where an LLM-powered agent analyzes requirements, retrieves relevant skill patterns from a vector store, generates code, runs tests, iterates on failures, and commits when coverage is met.

The existing skil catalog serves as the retrieval-augmented knowledge base. Agents do not generate from scratch — they generate against a retrieved context of known-good patterns.

### Goals

- Ingest Rally stories and produce committed, tested code with minimal human intervention
- Retrieve relevant skills semantically rather than by keyword
- Support iterative self-correction through a test-and-retry loop
- Provide full observability into every job's state, provider used, and failure points
- Support multiple LLM providers with graceful degradation

### Non-Goals (v1)

- Real-time Rally webhooks (polling only in v1)
- Docker-in-Docker sub-agent sandboxing (in-process subprocess in v1)
- Local LLM as a production fallback (experimental/low-priority)
- Multi-tenant or multi-user support
- Linking a feature to an existing repository (v1 always creates a new repo — see v2 Future Work)

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
    repo_url: str | None
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
    status_after: JobStatus      # JobState.status after this node ran
    provider: str | None         # e.g. "anthropic/claude-opus-4-7"; None for no-LLM nodes
    duration_ms: int             # wall-clock time for this node in milliseconds
    iteration: int               # codegen/interpret cycle index (0 for first pass)
    error: str | None            # first new error_log entry this node produced, if any
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
```

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

## 16. v2 Future Work

Items explicitly deferred from v1 scope. Do not implement until the POC is validated.

| Item | Description | Design Notes |
|------|-------------|--------------|
| Link feature to existing repo | `FeatureSpec` gets an optional `target_repo: str` field. `inject_node` sets `JobState.repo_name` and `JobState.repo_url` from it. `commit_node` already skips `create_repo()` when `repo_url` is non-None — no further changes needed there. | Low-risk schema addition; no graph changes |
| Rally v2 ingestion | Add a parser that reads Rally stories and produces `FeatureSpec` objects. The internal contract (`FeatureSpec`) is already stable. | Add `api/routes/rally.py`, keep `FeatureSpec` as the shared model |
| Parallel node fan-out | Use LangGraph `Send` API to dispatch `retrieve` and `analyze` simultaneously after `inject`. Cuts wall-clock time on the ANALYZE → CODE path roughly in half. | Requires LangGraph 0.2+ Send API; design already documented in Section 15 |
| Per-file code generation | Call `codegen_node` once per file in `file_plan` instead of batching all files in one call. Improves per-file quality at the cost of N LLM calls. | Requires graph fan-out (see above); Qwen Q1b prompt template is a valid starting point |
| Docker-in-Docker test sandboxing | Run generated code in an isolated container instead of a host tempdir subprocess. Prevents generated code from accessing host env vars, network, or filesystem. | Deferred — subprocess + tempdir is sufficient for v1 POC |

---

## 17. Decision Log

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
| 2026-04-19 | Added NodeTrace model + execution_trace to JobState | Per-node timing, provider, and error record needed for actionable debugging. Job worker owns population (already sees every node chunk with timing context) — nodes stay clean. Gives Claude, Codex, and human a single ordered list to diagnose slow/failing jobs. | Structured log-only approach (no queryable model); per-node self-reporting (adds coupling to every node) |
| 2026-04-19 | Linking features to existing repos deferred to v2 | v1 always creates a new GitHub repo. v2 will add optional `target_repo` field to `FeatureSpec`; `inject_node` would populate `JobState.repo_url` from it, bypassing `create_repo` in `commit_node`. No schema change required today — `commit_node` already skips creation when `repo_url` is set. | Adding `target_repo` to v1 FeatureSpec |
