from __future__ import annotations

from datetime import datetime
from functools import partial

from core.skills import SkillStore
from models.job import JobState, JobStatus

_TOP_K = 10


def retrieve_skills_node(state: JobState, store: SkillStore) -> dict:
    """
    Semantic search against ChromaDB to populate skills_pool.
    Query is built from the analyzed tech_stack + story description.
    No LLM call — pure vector retrieval.
    """
    query = _build_query(state)
    matches = store.query(query, n_results=_TOP_K)

    return {
        "skills_pool": matches,
        "status": JobStatus.SKILLS_RETRIEVED,
        "updated_at": datetime.utcnow(),
    }


def _build_query(state: JobState) -> str:
    """
    Combine tech stack signal with story description for a richer embedding query.
    Tech stack terms are prepended so they weight heavily in cosine similarity.
    """
    parts: list[str] = []

    if state.tech_stack:
        parts.append(" ".join(state.tech_stack))

    story = state.story_content
    for field in ("description", "name", "acceptance_criteria"):
        value = story.get(field, "")
        if value:
            parts.append(str(value))

    return " ".join(parts).strip() or state.story_id
