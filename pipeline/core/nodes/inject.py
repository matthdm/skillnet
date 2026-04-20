from __future__ import annotations

import re
from datetime import datetime

from models.job import JobState, JobStatus


def inject_node(state: JobState) -> dict:
    """
    First node in the pipeline. Validates the job and sets the target repo name.
    Does not call any LLM or external service.
    """
    story = state.story_content
    raw_name = story.get("name") or story.get("story_id") or state.story_id
    repo_name = _sanitize_repo_name(raw_name)

    return {
        "status": JobStatus.INJECTED,
        "repo_name": repo_name,
        "updated_at": datetime.utcnow(),
    }


def _sanitize_repo_name(raw: str) -> str:
    """Convert a story name to a valid GitHub repo name."""
    name = raw.lower().strip()
    name = re.sub(r"[^a-z0-9\-]", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    return name[:100] or "skillnet-job"
