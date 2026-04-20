from __future__ import annotations

from datetime import datetime
from functools import partial

from core.skills import SkillStore
from models.job import JobState, JobStatus

_TOP_K = 10


def retrieve_skills_node(state: JobState, store: SkillStore) -> dict:
    """
    Semantic search against ChromaDB to populate skills_pool.
    Idempotent — skips retrieval if skills_pool is already populated (re-queued after plan approval).
    """
    if state.skills_pool:
        return {"updated_at": datetime.utcnow()}

    query = _build_query(state)
    matches = store.query(query, n_results=_TOP_K)

    return {
        "skills_pool": matches,
        "status": JobStatus.SKILLS_RETRIEVED,
        "updated_at": datetime.utcnow(),
    }


_MAX_QUERY_CHARS = 400


def _build_query(state: JobState) -> str:
    """
    Build a retrieval query that aligns with skill-description language.

    Skills are indexed as pattern/how-to text. Raw feature descriptions are
    requirement language — they don't align semantically. We use analyze_node's
    summary (LLM-written technical paragraph) + tech stack instead, which is
    much closer to how skill bodies are written.

    Fallback to raw description if analysis hasn't run yet.
    """
    analysis = state.story_content.get("analysis", {})
    summary = analysis.get("summary", "")
    tech = ", ".join(state.tech_stack) if state.tech_stack else ""

    if summary:
        query = f"{tech}. {summary}" if tech else summary
    else:
        # pre-analysis fallback: use title + short description
        story = state.story_content
        title = story.get("title", state.story_id)
        desc = str(story.get("description", ""))[:200]
        query = f"{tech}. {title}. {desc}" if tech else f"{title}. {desc}"

    return query[:_MAX_QUERY_CHARS].strip()
