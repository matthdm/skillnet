from __future__ import annotations

import os
import re
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse
from redis import asyncio as redis

from models.job import JobState, JobStatus
from models.skill import FeatureSpec

router = APIRouter()


@router.post("/feature")
async def ingest_feature(spec: FeatureSpec) -> dict:
    """Accept a pre-parsed FeatureSpec JSON and enqueue a pipeline job."""
    return await _enqueue(spec)


@router.post("/feature/markdown")
async def ingest_feature_markdown(body: str = Body(..., media_type="text/plain")) -> dict:
    """Accept raw markdown, parse into FeatureSpec, and enqueue a pipeline job."""
    try:
        spec = _parse_markdown(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _enqueue(spec)


async def _enqueue(spec: FeatureSpec) -> dict:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)

    job_id = str(uuid4())
    state = JobState(
        job_id=job_id,
        story_id=spec.feature_id,
        story_content=spec.model_dump(),
        status=JobStatus.PENDING,
    )

    try:
        already_seen = await redis_client.sismember("features:seen", spec.feature_id)
        if already_seen:
            return JSONResponse(
                status_code=409,
                content={"job_id": None, "status": "duplicate detected", "feature_id": spec.feature_id},
            )

        await redis_client.set(f"job:{job_id}", state.model_dump_json())
        await redis_client.sadd("jobs:all", job_id)
        await redis_client.sadd("features:seen", spec.feature_id)
        await redis_client.rpush("jobs:queue", job_id)  # enqueue last — state must exist first
    finally:
        await redis_client.aclose()

    return {"job_id": job_id, "status": "queued", "feature_id": spec.feature_id}


def _parse_markdown(content: str) -> FeatureSpec:
    """
    Parse a feature markdown file into a FeatureSpec.

    Expected format:
        # FEAT-1234: Feature Title

        ## Description
        ...

        ## Acceptance Criteria
        - item 1

        ## Tech Stack (optional)
        - Python
    """
    lines = content.strip().splitlines()

    feature_id, title = _parse_header(lines)
    sections = _extract_sections(lines)

    description = sections.get("description", "").strip()
    acceptance_criteria = sections.get("acceptance criteria", "").strip()
    tech_stack_raw = sections.get("tech stack", "")
    tech_stack_hint = [
        line.lstrip("-•* ").strip()
        for line in tech_stack_raw.splitlines()
        if line.strip().lstrip("-•* ")
    ]

    if not description:
        raise ValueError("Markdown must contain a ## Description section")

    return FeatureSpec(
        feature_id=feature_id,
        title=title,
        description=description,
        acceptance_criteria=acceptance_criteria,
        tech_stack_hint=tech_stack_hint,
        source="markdown",
    )


def _parse_header(lines: list[str]) -> tuple[str, str]:
    for line in lines:
        if line.startswith("# "):
            header = line[2:].strip()
            match = re.match(r"^([\w-]+):\s*(.+)$", header)
            if match:
                return match.group(1).strip(), match.group(2).strip()
            return header, header
    raise ValueError("Markdown must start with '# FEAT-ID: Title'")


def _extract_sections(lines: list[str]) -> dict[str, str]:
    """Split markdown into sections keyed by lowercased heading (without ##)."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            heading = line[3:].strip()
            # Normalize: strip parenthetical notes like "(optional)"
            current = re.sub(r"\s*\(.*?\)", "", heading).strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)

    if current is not None:
        sections[current] = "\n".join(buf).strip()

    return sections
