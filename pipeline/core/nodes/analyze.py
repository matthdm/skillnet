from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from core.token_tracker import TokenUsageHandler
from models.job import JobState, JobStatus

logger = logging.getLogger(__name__)

_TIMEOUT = 90  # seconds

_SYSTEM = """You are a senior software architect specializing in technology stack analysis and \
system design. Your role is to deconstruct feature requirements into actionable technical plans.

Output ONLY valid JSON. No markdown code blocks, no introductory text, no explanations.
Ensure the following keys are present in every response:
- tech_stack: A list of 3-5 specific technologies inferred from requirements (e.g. "FastAPI", "Redis").
- success_criteria: Testable assertions derived directly from acceptance criteria.
- file_plan: File paths relative to the project root that must be created (e.g. "app/main.py").
- summary: A single paragraph plain-English summary of the core technical implementation strategy."""

_USER_TEMPLATE = """Analyze the following feature specification and generate a technical \
implementation plan in JSON format.

Feature ID: {feature_id}
Title: {title}
Description: {description}
Acceptance Criteria: {acceptance_criteria}
Tech Stack Hints (optional): {tech_stack_hint}

Return the JSON object strictly adhering to the output requirements defined in your system instructions."""


class AnalysisResult(BaseModel):
    tech_stack: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    file_plan: list[str] = Field(default_factory=list)
    summary: str = ""


async def analyze_node(state: JobState, llm: BaseChatModel, provider_label: str) -> dict:
    story = state.story_content
    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=_USER_TEMPLATE.format(
            feature_id=story.get("feature_id", state.story_id),
            title=story.get("title", state.story_id),
            description=story.get("description", ""),
            acceptance_criteria=story.get("acceptance_criteria", ""),
            tech_stack_hint=", ".join(story.get("tech_stack_hint", [])) or "none provided",
        )),
    ]

    try:
        handler = TokenUsageHandler()
        structured_llm = llm.with_structured_output(AnalysisResult)
        result: AnalysisResult = await asyncio.wait_for(
            structured_llm.ainvoke(messages, config={"callbacks": [handler]}),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("analyze_node timed out after %ds for job %s", _TIMEOUT, state.job_id)
        return {
            "status": JobStatus.PAUSED,
            "paused_at_node": "analyze",
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"analyze_node timed out after {_TIMEOUT}s"],
        }
    except Exception as exc:
        logger.exception("analyze_node failed for job %s: %s", state.job_id, exc)
        return {
            "status": JobStatus.FAILED,
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"analyze_node error: {exc}"],
        }

    updated_story = {**state.story_content, "analysis": result.model_dump()}

    return {
        "tech_stack": result.tech_stack,
        "story_content": updated_story,
        "status": JobStatus.ANALYZED,
        "provider_log": state.provider_log + [f"analyze:{provider_label}"],
        "updated_at": datetime.utcnow(),
        "_input_tokens": handler.input_tokens,
        "_output_tokens": handler.output_tokens,
    }
