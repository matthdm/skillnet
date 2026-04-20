from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis import asyncio as aioredis

from api.routes import admin, ingest, jobs, projects
from config import load_llm_config
from core.graph import build_graph
from core.llm_router import LLMRouter
from core.repo_manager import RepoManager
from core.skills import SkillStore
from core.token_tracker import estimate_cost
from logging_config import setup_logging
from models.job import JobState, JobStatus, NodeTrace

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_CONFIG_PATH = None  # uses default config/llm.yaml relative to package


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    chroma_path = os.environ.get("CHROMA_DATA_PATH", "./chroma_data")

    # ── LLM router + embeddings ──────────────────────────────────────────
    config = load_llm_config()
    router = LLMRouter(config)
    embeddings = router.get_embeddings()

    # ── Skill store (ChromaDB) ───────────────────────────────────────────
    store = SkillStore(embeddings=embeddings, data_path=chroma_path)
    if store.count() == 0:
        logger.warning(
            "ChromaDB skill collection is empty. "
            "Run: python pipeline/scripts/ingest_skills.py to populate it."
        )
    else:
        logger.info("Skill store ready: %d skills indexed.", store.count())

    # ── Repo manager ─────────────────────────────────────────────────────
    repo_manager = RepoManager()

    # ── LangGraph with Redis checkpointer ────────────────────────────────
    checkpointer_cm = None
    try:
        from langgraph.checkpoint.redis import AsyncRedisSaver
        checkpointer_cm = AsyncRedisSaver.from_conn_string(redis_url)
        checkpointer = await checkpointer_cm.__aenter__()
        logger.info("Redis checkpointer ready.")
    except (ImportError, Exception) as exc:
        logger.warning("AsyncRedisSaver unavailable (%s) — using InMemorySaver.", exc)
        from langgraph.checkpoint.memory import InMemorySaver
        checkpointer = InMemorySaver()
        checkpointer_cm = None

    graph = build_graph(router=router, store=store, repo_manager=repo_manager, checkpointer=checkpointer)

    # ── Store in app.state ───────────────────────────────────────────────
    app.state.router = router
    app.state.store = store
    app.state.graph = graph
    app.state.redis_url = redis_url

    # ── Background job worker ─────────────────────────────────────────────
    worker_task = asyncio.create_task(_job_worker(app))

    logger.info("Skillnet pipeline API ready.")
    yield

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    if checkpointer_cm is not None:
        await checkpointer_cm.__aexit__(None, None, None)


async def _job_worker(app: FastAPI) -> None:
    """
    Dequeues job IDs from jobs:queue and runs each through the LangGraph pipeline.
    Writes state back to Redis after every node via astream so the dashboard
    reflects intermediate progress.
    """
    redis_url = app.state.redis_url
    graph = app.state.graph

    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        while True:
            result = await redis_client.blpop("jobs:queue", timeout=2)
            if result is None:
                continue

            _, job_id = result
            raw = await redis_client.get(f"job:{job_id}")
            if raw is None:
                logger.warning("Job %s not found in Redis — skipping.", job_id)
                continue

            state = JobState.model_validate_json(raw)
            logger.info("Processing job %s (feature %s)", job_id, state.story_id)

            try:
                config = {"configurable": {"thread_id": job_id}}
                t_node_start = time.monotonic()
                async for chunk in graph.astream(state, config=config):
                    t_node_end = time.monotonic()
                    node_name = next(iter(chunk))
                    raw_output = dict(chunk[node_name])
                    input_tokens = int(raw_output.pop("_input_tokens", 0))
                    output_tokens = int(raw_output.pop("_output_tokens", 0))
                    updated = state.model_copy(update=raw_output)
                    duration_ms = int((t_node_end - t_node_start) * 1000)
                    t_node_start = t_node_end

                    new_error = next(
                        (e for e in updated.error_logs if e not in state.error_logs), None
                    )
                    provider = next(
                        (p for p in updated.provider_log if p not in state.provider_log), None
                    )
                    trace_entry = NodeTrace(
                        node=node_name,
                        status_after=updated.status.value,
                        provider=provider,
                        duration_ms=duration_ms,
                        iteration=updated.iteration_count,
                        error=new_error,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=estimate_cost(provider, input_tokens, output_tokens),
                    )
                    updated = updated.model_copy(
                        update={"execution_trace": updated.execution_trace + [trace_entry]}
                    )
                    await redis_client.set(f"job:{job_id}", updated.model_dump_json())
                    state = updated
                    logger.info(
                        "Job %s — node '%s' → %s (%dms)",
                        job_id, node_name, state.status.value, duration_ms,
                        extra={"job_id": job_id, "node": node_name},
                    )

            except Exception as exc:
                logger.exception("Job %s failed with unhandled exception: %s", job_id, exc)
                failed_state = state.model_copy(update={
                    "status": JobStatus.FAILED,
                    "error_logs": state.error_logs + [f"Unhandled error: {exc}"],
                })
                await redis_client.set(f"job:{job_id}", failed_state.model_dump_json())
    finally:
        await redis_client.aclose()


app = FastAPI(title="Skillnet Pipeline", version="0.1.0", lifespan=lifespan)

app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(projects.router, prefix="/projects", tags=["projects"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])


@app.get("/health")
async def health() -> dict:
    skill_count = getattr(app.state, "store", None)
    count = skill_count.count() if skill_count else 0
    return {"status": "ok", "skills_indexed": count}
