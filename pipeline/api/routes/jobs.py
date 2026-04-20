# CODEX TASK C4 â€” implement this file exactly as specified
# GET /jobs/{job_id} â€” returns JobState from Redis hash "job:{job_id}"
# GET /jobs â€” returns list of all job_ids from Redis set "jobs:all" with their status field
# Return 404 if job not found.

from __future__ import annotations

import os
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from redis import asyncio as redis

from models.job import JobState, JobStatus, NodeTrace

router = APIRouter()


class RejectPlanRequest(BaseModel):
    reason: str = ""


class RetryRequest(BaseModel):
    patch_instructions: str = ""


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


@router.get("/{job_id}/trace", response_model=list[NodeTrace])
async def get_job_trace(job_id: str) -> list[NodeTrace]:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        payload = await redis_client.get(f"job:{job_id}")
    finally:
        await redis_client.aclose()

    if payload is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    state = JobState.model_validate_json(payload)
    return state.execution_trace


@router.post("/{job_id}/approve-plan")
async def approve_plan(job_id: str) -> dict:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        payload = await redis_client.get(f"job:{job_id}")
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        state = JobState.model_validate_json(payload)
        if state.status != JobStatus.PLANNING:
            raise HTTPException(status_code=409, detail=f"Job is not in PLANNING state: {state.status}")
        if state.implementation_plan is None:
            raise HTTPException(status_code=409, detail="Job has no implementation plan")

        state.implementation_plan.status = "approved"
        state.status = JobStatus.SKILLS_RETRIEVED  # re-enters graph; skips to codegen via routing
        state.updated_at = datetime.utcnow()

        await redis_client.set(f"job:{job_id}", state.model_dump_json())
        await redis_client.rpush("jobs:queue", job_id)
        return {"job_id": job_id, "status": "approved", "queued": True}
    finally:
        await redis_client.aclose()


@router.post("/{job_id}/reject-plan")
async def reject_plan(job_id: str, body: RejectPlanRequest) -> dict:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        payload = await redis_client.get(f"job:{job_id}")
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        state = JobState.model_validate_json(payload)
        if state.status != JobStatus.PLANNING:
            raise HTTPException(status_code=409, detail=f"Job is not in PLANNING state: {state.status}")

        state.implementation_plan.status = "rejected"
        state.implementation_plan.rejection_reason = body.reason or None
        state.status = JobStatus.REJECTED
        state.updated_at = datetime.utcnow()

        await redis_client.set(f"job:{job_id}", state.model_dump_json())
        return {"job_id": job_id, "status": "rejected"}
    finally:
        await redis_client.aclose()


@router.post("/{job_id}/resume")
async def resume_job(job_id: str) -> dict:
    """Re-queue a PAUSED job. Idempotency guards in each node handle skipping completed work."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        payload = await redis_client.get(f"job:{job_id}")
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        state = JobState.model_validate_json(payload)
        if state.status != JobStatus.PAUSED:
            raise HTTPException(status_code=409, detail=f"Job is not PAUSED (status: {state.status})")

        state.status = JobStatus.PENDING
        state.paused_at_node = None
        state.updated_at = datetime.utcnow()

        await redis_client.set(f"job:{job_id}", state.model_dump_json())
        await redis_client.rpush("jobs:queue", job_id)
        return {"job_id": job_id, "status": "queued"}
    finally:
        await redis_client.aclose()


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, body: RetryRequest) -> dict:
    """Clone a FAILED or EXHAUSTED job as a fresh job, preserving context from the prior run."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        payload = await redis_client.get(f"job:{job_id}")
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        original = JobState.model_validate_json(payload)
        if original.status not in {JobStatus.FAILED, JobStatus.EXHAUSTED}:
            raise HTTPException(
                status_code=409,
                detail=f"Job must be FAILED or EXHAUSTED to retry (status: {original.status})",
            )

        new_job_id = str(uuid4())

        # Carry forward story_content, injecting patch instructions and prior error context
        story_content = dict(original.story_content)
        if body.patch_instructions.strip():
            story_content["patch_instructions"] = body.patch_instructions.strip()
        if original.error_logs:
            story_content["prior_error_context"] = original.error_logs[-5:]

        # Carry forward the approved plan so we skip the PLANNING pause on retry
        prior_plan = original.implementation_plan
        if prior_plan is not None:
            prior_plan = prior_plan.model_copy(update={"status": "approved", "rejection_reason": None})

        new_state = JobState(
            job_id=new_job_id,
            story_id=original.story_id,
            story_content=story_content,
            tech_stack=original.tech_stack,
            skills_pool=original.skills_pool,
            repo_name=original.repo_name,
            repo_url=original.repo_url,
            implementation_plan=prior_plan,
            job_type=original.job_type,
            project_id=original.project_id,
            parent_job_id=job_id,
            max_iterations=original.max_iterations,
            status=JobStatus.PENDING,
        )

        await redis_client.set(f"job:{new_job_id}", new_state.model_dump_json())
        await redis_client.sadd("jobs:all", new_job_id)
        await redis_client.rpush("jobs:queue", new_job_id)
        return {"job_id": new_job_id, "status": "queued", "parent_job_id": job_id}
    finally:
        await redis_client.aclose()


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
                    "feature_id": state.story_id,
                    "title": str(state.story_content.get("title", "")),
                    "created_at": state.created_at.isoformat(),
                    "parent_job_id": getattr(state, "parent_job_id", None)
                    or state.story_content.get("parent_job_id"),
                }
            )
    finally:
        await redis_client.aclose()

    return rows
