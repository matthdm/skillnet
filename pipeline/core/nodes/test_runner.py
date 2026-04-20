# CLAUDE — owns this file. No LLM — subprocess execution + output parsing.
from __future__ import annotations
from models.job import JobState


def test_node(state: JobState) -> JobState:
    raise NotImplementedError("test_node — to be implemented by Claude")
