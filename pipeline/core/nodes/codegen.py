from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from models.job import JobState, JobStatus

logger = logging.getLogger(__name__)

_TIMEOUT = 120  # seconds
_MAX_SKILL_DESC_CHARS = 300
_MAX_TOP_SKILLS = 3

_SYSTEM = """You are a senior software engineer focused on production-quality code generation. \
Your role is to implement all required files based on an architectural plan and skill patterns.

Output ONLY valid JSON. No markdown wrappers, no comments outside JSON, no conversational text.
Generate ALL files in a single response as a JSON object where each key is a relative file path \
and each value is the complete raw source code for that file. Every file must be fully implemented \
— no TODOs, no stubs, no placeholder comments."""

_USER_TEMPLATE = """Generate all files listed in the file plan based on the provided context.

Feature Summary:
{summary}

Files to generate:
{file_plan}

Relevant skill references (max 300 chars each):
{skill_refs}

{fix_context}Return a raw JSON object: {{ "path/to/file.py": "# complete file content\\n..." }}"""

_FIX_TEMPLATE = """Previous attempt failed. Fix the following errors:
{errors}

"""


class CodegenResult(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)


async def codegen_node(state: JobState, llm: BaseChatModel, provider_label: str) -> dict:
    analysis = state.story_content.get("analysis", {})
    summary = analysis.get("summary", state.story_content.get("description", ""))
    file_plan = analysis.get("file_plan", [])

    skill_refs = "\n".join(
        f"- {m.skill.name}: {m.skill.description[:_MAX_SKILL_DESC_CHARS]}"
        for m in state.skills_pool[:_MAX_TOP_SKILLS]
    ) or "none"

    fix_context = ""
    if state.iteration_count > 0 and state.error_logs:
        recent_errors = "\n".join(state.error_logs[-5:])
        fix_context = _FIX_TEMPLATE.format(errors=recent_errors)

    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=_USER_TEMPLATE.format(
            summary=summary,
            file_plan="\n".join(f"  - {f}" for f in file_plan),
            skill_refs=skill_refs,
            fix_context=fix_context,
        )),
    ]

    try:
        structured_llm = llm.with_structured_output(CodegenResult)
        result: CodegenResult = await asyncio.wait_for(
            structured_llm.ainvoke(messages),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("codegen_node timed out after %ds for job %s", _TIMEOUT, state.job_id)
        return {
            "status": JobStatus.PAUSED,
            "paused_at_node": "codegen",
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"codegen_node timed out after {_TIMEOUT}s"],
        }
    except Exception as exc:
        logger.exception("codegen_node failed for job %s: %s", state.job_id, exc)
        return {
            "status": JobStatus.FAILED,
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"codegen_node error: {exc}"],
        }

    return {
        "generated_files": result.files,
        "status": JobStatus.CODING,
        "iteration_count": state.iteration_count + 1,
        "provider_log": state.provider_log + [f"codegen:{provider_label}"],
        "updated_at": datetime.utcnow(),
    }
