# CODEX TASK C4 â€” implement this file exactly as specified
# GET /jobs/{job_id} â€” returns JobState from Redis hash "job:{job_id}"
# GET /jobs â€” returns list of all job_ids from Redis set "jobs:all" with their status field
# Return 404 if job not found.

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from redis import asyncio as redis

from models.job import JobState

router = APIRouter()


@router.get("/{job_id}", response_model=JobState)
async def get_job(job_id: str) -> JobState:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        payload = await redis_client.get(f"job:{job_id}")
    finally:
        await redis_client.aclose()

    if payload is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return JobState.model_validate_json(payload)


@router.get("/")
async def list_jobs() -> list[dict]:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)

    try:
        job_ids = sorted(await redis_client.smembers("jobs:all"))
        rows: list[dict] = []
        for job_id in job_ids:
            payload = await redis_client.get(f"job:{job_id}")
            if payload is None:
                continue
            state = JobState.model_validate_json(payload)
            rows.append(
                {
                    "job_id": job_id,
                    "status": state.status.value,
                    "degraded": state.degraded,
                }
            )
    finally:
        await redis_client.aclose()

    return rows
