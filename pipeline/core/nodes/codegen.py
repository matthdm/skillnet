# CLAUDE — owns this file. LLM Tier: MEDIUM
from __future__ import annotations
from langchain_core.language_models import BaseChatModel
from models.job import JobState


def codegen_node(state: JobState, llm: BaseChatModel) -> JobState:
    raise NotImplementedError("codegen_node — to be implemented by Claude")
