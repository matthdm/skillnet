# CLAUDE — owns this file. LLM Tier: HIGH
from __future__ import annotations
from langchain_core.language_models import BaseChatModel
from models.job import JobState


def analyze_node(state: JobState, llm: BaseChatModel) -> JobState:
    raise NotImplementedError("analyze_node — to be implemented by Claude")
