from __future__ import annotations

import os
import re
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from redis import asyncio as redis

from models.job import JobState
from models.project import Project

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    repo_url: str | None = None
    project_id: str | None = None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug


async def _next_project_id(redis_client, base_name: str) -> str:
    base = _slugify(base_name) or f"proj-{uuid4().hex[:8]}"
    candidate = base
    suffix = 1
    while await redis_client.sismember("projects:all", candidate):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


@router.post("/", status_code=201)
async def create_project(payload: CreateProjectRequest) -> dict:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        project_id = payload.project_id or await _next_project_id(redis_client, payload.name)
        if await redis_client.sismember("projects:all", project_id):
            raise HTTPException(status_code=409, detail=f"Project already exists: {project_id}")

        project = Project(
            project_id=project_id,
            name=payload.name,
            description=payload.description,
            repo_url=payload.repo_url,
        )

        await redis_client.set(f"project:{project_id}", project.model_dump_json())
        await redis_client.sadd("projects:all", project_id)
        return {"project_id": project_id}
    finally:
        await redis_client.aclose()


@router.get("/")
async def list_projects() -> list[dict]:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        project_ids = sorted(await redis_client.smembers("projects:all"))
        items: list[dict] = []
        for project_id in project_ids:
            payload = await redis_client.get(f"project:{project_id}")
            if payload is None:
                continue
            project = Project.model_validate_json(payload)
            items.append(
                {
                    "project_id": project.project_id,
                    "name": project.name,
                    "description": project.description,
                    "repo_url": project.repo_url,
                    "feature_count": len(project.feature_ids),
                }
            )
        return items
    finally:
        await redis_client.aclose()


@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: str) -> Project:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        payload = await redis_client.get(f"project:{project_id}")
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return Project.model_validate_json(payload)
    finally:
        await redis_client.aclose()


@router.get("/{project_id}/jobs")
async def get_project_jobs(project_id: str) -> list[dict]:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    try:
        project_payload = await redis_client.get(f"project:{project_id}")
        if project_payload is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        job_ids = sorted(await redis_client.smembers("jobs:all"))
        rows: list[dict] = []
        for job_id in job_ids:
            payload = await redis_client.get(f"job:{job_id}")
            if payload is None:
                continue
            state = JobState.model_validate_json(payload)
            if state.project_id != project_id:
                continue

            rows.append(
                {
                    "job_id": job_id,
                    "feature_id": state.story_id,
                    "status": state.status.value,
                    "created_at": state.created_at.isoformat(),
                    "updated_at": state.updated_at.isoformat(),
                    "degraded": state.degraded,
                    "pr_url": state.pr_url,
                }
            )
        return rows
    finally:
        await redis_client.aclose()
