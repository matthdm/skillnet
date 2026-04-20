# CODEX TASK C4 â€” implement this file exactly as specified
# POST /ingest/rally â€” accepts RallyStory JSON, validates with Pydantic, enqueues job.
# Return: {"job_id": str, "status": "queued"}
# Enqueue by writing job_id to Redis list "jobs:queue" and job state to Redis hash "job:{job_id}"
# Use redis.asyncio for async Redis access.

from __future__ import annotations

import os
from uuid import uuid4

from fastapi import APIRouter
from redis import asyncio as redis

from models.job import JobState, JobStatus
from models.skill import RallyStory

router = APIRouter()


@router.post("/rally")
async def ingest_rally(story: RallyStory) -> dict:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)

    job_id = str(uuid4())
    state = JobState(
        job_id=job_id,
        story_id=story.story_id,
        story_content=story.model_dump(),
        status=JobStatus.PENDING,
    )

    try:
        await redis_client.rpush("jobs:queue", job_id)
        await redis_client.set(f"job:{job_id}", state.model_dump_json())
        await redis_client.sadd("jobs:all", job_id)
    finally:
        await redis_client.aclose()

    return {"job_id": job_id, "status": "queued"}
