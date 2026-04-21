from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from redis import asyncio as aioredis

from scripts.ingest_skills import load_all_skills

logger = logging.getLogger(__name__)
router = APIRouter()


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379")


@dataclass
class IngestionStatus:
    state: str = "idle"          # "idle" | "running" | "complete" | "failed"
    skills_processed: int = 0
    skills_total: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "skills_processed": self.skills_processed,
            "skills_total": self.skills_total,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }


_status = IngestionStatus()


@router.post("/ingest-skills", status_code=202)
async def trigger_ingestion(request: Request) -> dict:
    if _status.state == "running":
        raise HTTPException(status_code=409, detail="Ingestion already running.")

    store = request.app.state.store
    _status.state = "running"
    _status.skills_processed = 0
    _status.skills_total = 0
    _status.started_at = datetime.utcnow()
    _status.completed_at = None
    _status.error = None

    asyncio.create_task(_run_ingestion(store))
    logger.info("Skill ingestion triggered via admin endpoint.")
    return {"status": "started"}


@router.get("/ingest-skills/status")
async def ingestion_status() -> dict:
    return _status.to_dict()


async def _run_ingestion(store) -> None:
    try:
        skill_dicts = await asyncio.to_thread(load_all_skills)
        _status.skills_total = len(skill_dicts)
        logger.info("Ingestion: loaded %d skills, starting embedding.", _status.skills_total)

        def _on_batch(processed: int, total: int) -> None:
            _status.skills_processed = processed
            logger.info("Ingestion progress: %d / %d", processed, total)

        await asyncio.to_thread(store.embed_and_upsert, skill_dicts, _on_batch)

        _status.state = "complete"
        _status.skills_processed = _status.skills_total
        _status.completed_at = datetime.utcnow()
        logger.info("Ingestion complete: %d skills indexed.", _status.skills_total)

    except Exception as exc:
        _status.state = "failed"
        _status.error = str(exc)
        _status.completed_at = datetime.utcnow()
        logger.exception("Ingestion failed: %s", exc)


# ── Redis management ─────────────────────────────────────────────────────────

@router.get("/redis/stats")
async def redis_stats() -> dict:
    """Queue depth, job counts, and dedup set size."""
    client = aioredis.from_url(_redis_url(), decode_responses=True)
    try:
        queue_len = await client.llen("jobs:queue")
        total_jobs = await client.scard("jobs:all")
        dedup_count = await client.scard("features:seen")
        dedup_members = await client.smembers("features:seen")
        return {
            "queue_depth": queue_len,
            "total_jobs": total_jobs,
            "dedup_count": dedup_count,
            "seen_feature_ids": sorted(dedup_members),
        }
    finally:
        await client.aclose()


@router.delete("/redis/dedup/{feature_id}", status_code=200)
async def clear_dedup_entry(feature_id: str) -> dict:
    """Remove a single feature_id from the dedup set so it can be resubmitted."""
    client = aioredis.from_url(_redis_url(), decode_responses=True)
    try:
        removed = await client.srem("features:seen", feature_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"{feature_id} not in dedup set")
        logger.info("Cleared dedup entry: %s", feature_id)
        return {"cleared": feature_id}
    finally:
        await client.aclose()


@router.delete("/redis/dedup", status_code=200)
async def clear_all_dedup() -> dict:
    """Clear the entire features:seen dedup set."""
    client = aioredis.from_url(_redis_url(), decode_responses=True)
    try:
        count = await client.scard("features:seen")
        await client.delete("features:seen")
        logger.info("Cleared all dedup entries (%d).", count)
        return {"cleared_count": count}
    finally:
        await client.aclose()


@router.delete("/redis/jobs", status_code=200)
async def clear_all_jobs() -> dict:
    """
    Remove all job keys, the jobs:all set, and the jobs:queue list.
    Does NOT touch features:seen — use DELETE /admin/redis/dedup for that.
    """
    client = aioredis.from_url(_redis_url(), decode_responses=True)
    try:
        job_ids = await client.smembers("jobs:all")
        pipeline = client.pipeline()
        for job_id in job_ids:
            pipeline.delete(f"job:{job_id}")
        pipeline.delete("jobs:all")
        pipeline.delete("jobs:queue")
        await pipeline.execute()
        logger.info("Cleared all jobs (%d).", len(job_ids))
        return {"cleared_jobs": len(job_ids)}
    finally:
        await client.aclose()


@router.get("/logs")
async def get_logs(n: int = 200, level: str = "", job_id: str = "") -> dict:
    """Return the last N lines from the in-memory log buffer."""
    from logging_config import log_buffer
    lines = list(log_buffer)
    if level:
        lines = [l for l in lines if l.get("level", "").upper() == level.upper()]
    if job_id:
        lines = [l for l in lines if l.get("job_id") == job_id]
    return {"lines": lines[-n:], "total": len(lines)}
