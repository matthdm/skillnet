# CLAUDE — owns this file. No LLM — GitHub API calls via RepoManager.
from __future__ import annotations
from models.job import JobState


def commit_node(state: JobState) -> JobState:
    raise NotImplementedError("commit_node — to be implemented by Claude")
