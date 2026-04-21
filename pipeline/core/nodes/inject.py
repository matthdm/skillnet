from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime

from core.repo_manager import RepoManager
from models.job import JobState, JobStatus

logger = logging.getLogger(__name__)

_NEW_SERVICE_SCAFFOLD_HINT = (
    "This is a new_service job — a complete standalone service. "
    "The file plan MUST include: a Dockerfile, a config module "
    "(e.g. app/config.py), an application entrypoint (e.g. app/main.py), "
    "and a README.md. Do not omit any of these."
)


async def inject_node(state: JobState) -> dict:
    """
    First node in the pipeline. Validates the job, sets repo_name, and injects
    job-type-specific context into story_content.
    Idempotent — skips if already past PENDING (e.g. re-queued after plan approval).
    """
    if state.status != JobStatus.PENDING:
        return {"updated_at": datetime.utcnow()}

    story = state.story_content
    job_type = state.job_type

    # Determine repo name
    if job_type == "change_request" and story.get("target_repo"):
        # Strip "owner/" prefix if user entered "owner/repo" format
        raw_target = str(story["target_repo"])
        repo_name = _sanitize_repo_name(raw_target.split("/")[-1])
    else:
        raw_name = story.get("repo_name") or story.get("title") or state.story_id
        repo_name = _sanitize_repo_name(raw_name)

    updates: dict = {
        "status": JobStatus.INJECTED,
        "repo_name": repo_name,
        "updated_at": datetime.utcnow(),
    }

    if job_type == "new_service":
        updates["story_content"] = {**story, "scaffold_hint": _NEW_SERVICE_SCAFFOLD_HINT}

    elif job_type == "change_request" and story.get("target_repo"):
        # Use the sanitized repo_name (already stripped of owner prefix above)
        target_repo = repo_name
        owner = os.environ.get("GITHUB_OWNER", "")
        repo_url = f"https://github.com/{owner}/{target_repo}"
        updates["repo_url"] = repo_url

        try:
            repo_manager = RepoManager()
            existing_files = await asyncio.to_thread(repo_manager.get_file_tree, target_repo)
            updates["story_content"] = {**story, "existing_files": existing_files}
            logger.info(
                "inject_node job %s: fetched %d files from %s",
                state.job_id, len(existing_files), target_repo,
            )
        except Exception as exc:
            logger.warning(
                "inject_node job %s: could not fetch file tree for %s: %s",
                state.job_id, target_repo, exc,
            )

    return updates


def _sanitize_repo_name(raw: str) -> str:
    """Convert a story name to a valid GitHub repo name."""
    name = raw.lower().strip()
    name = re.sub(r"[^a-z0-9\-]", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    return name[:100] or "skillnet-job"
