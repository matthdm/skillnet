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

Ingestion script: `scripts/ingest_skills.py`
Run manually or at container startup if Redis is empty.

---

## 10. Rally Integration

### v1: Polling

- `GET /rally/slm/webservice/v2.0/hierarchicalrequirement` filtered by status and modified date
- Poll interval: configurable, default 60 seconds
- Deduplication: store `story_id` in Redis set `rally:seen_stories`
- Auth: Rally API key via `ZSESSIONID` header

### v2: Webhooks (future)

- Rally supports webhook subscriptions via their REST API
- Requires a publicly accessible endpoint (ngrok for dev, load balancer for prod)
- Not in scope for v1

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
RALLY_API_KEY=
RALLY_WORKSPACE_URL=
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
| 1 | Rally workspace URL and auth method confirmation | Human | Open |
| 2 | GitHub org vs personal account for repo creation | Human | Open |
| 3 | Max file size for GitHub Contents API vs requiring local git | Claude | Open |
| 4 | Test runner strategy — what test framework do generated projects use? | Claude | Open |

---

## 15. Decision Log

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
