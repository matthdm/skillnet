from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from core.token_tracker import TokenUsageHandler, estimate_cost
from models.job import ImplementationPlan, JobState, JobStatus, PlanFile

logger = logging.getLogger(__name__)

_TIMEOUT = 60  # seconds
_COST_INPUT_PER_M = {"opus": 15.0, "sonnet": 3.0, "haiku": 0.25}
_COST_OUTPUT_PER_M = {"opus": 75.0, "sonnet": 15.0, "haiku": 1.25}

_SYSTEM = """You are a senior software architect producing a concise implementation plan.
Your plan will be reviewed by a human before any code is written.

Output ONLY valid JSON matching the schema. No markdown, no extra keys.

Schema:
{
  "requirements_brief": "string ≤100 words — restate in your own words what must be built and why",
  "approach": "string ≤150 words — how you will build it: key design choices, patterns used, why",
  "files": [
    {"path": "relative/path.py", "action": "create|modify|delete", "description": "≤20 words"}
  ],
  "estimated_input_tokens": integer,
  "estimated_output_tokens": integer,
  "estimated_cost_usd": float
}

Token estimation guide (for the CODEGEN call that will follow this plan):
- Base input overhead: ~600 tokens (system prompt + feature summary + skill refs)
- Per file to CREATE: add ~50 tokens input, ~400 tokens output (avg 100 lines × 4 tok/line)
- Per file to MODIFY: add ~200 tokens input (existing content), ~300 tokens output
- Change request with existing file tree: add ~300 tokens input
- Cost: use MEDIUM tier pricing ($3.00/M input, $15.00/M output)
- Round to nearest 100 tokens"""

_USER_TEMPLATE = """Produce an implementation plan for the following feature.

Feature ID: {feature_id}
Title: {title}
Job type: {job_type}
{target_repo_line}
Description:
{description}

Acceptance Criteria:
{acceptance_criteria}

Tech Stack (from analysis):
{tech_stack}

Planned files (from analysis):
{file_plan}

Retrieved skill patterns (top matches):
{skill_refs}
{existing_files_section}
Return the JSON plan."""


def _skill_refs(state: JobState) -> str:
    lines = []
    for m in state.skills_pool[:3]:
        lines.append(f"- {m.skill.name}: {m.skill.description[:200]}")
    return "\n".join(lines) or "none"


def _existing_files_section(state: JobState) -> str:
    existing = state.story_content.get("existing_files", [])
    if not existing:
        return ""
    listing = "\n".join(f"  {f}" for f in existing[:50])
    return f"\nExisting files in target repo:\n{listing}\n"


async def plan_node(state: JobState, llm: BaseChatModel, provider_label: str) -> dict:
    story = state.story_content
    analysis = story.get("analysis", {})

    target_repo_line = (
        f"Target repo: {story.get('target_repo', '')}" if story.get("target_repo") else ""
    )

    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=_USER_TEMPLATE.format(
            feature_id=story.get("feature_id", state.story_id),
            title=story.get("title", state.story_id),
            job_type=state.job_type,
            target_repo_line=target_repo_line,
            description=story.get("description", "")[:600],
            acceptance_criteria=story.get("acceptance_criteria", "")[:800],
            tech_stack=", ".join(state.tech_stack) or "not specified",
            file_plan="\n".join(f"  - {f}" for f in analysis.get("file_plan", [])) or "  (none yet)",
            skill_refs=_skill_refs(state),
            existing_files_section=_existing_files_section(state),
        )),
    ]

    try:
        handler = TokenUsageHandler()

        class _PlanResult(ImplementationPlan):
            pass

        structured_llm = llm.with_structured_output(_PlanResult)
        result: ImplementationPlan = await asyncio.wait_for(
            structured_llm.ainvoke(messages, config={"callbacks": [handler]}),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("plan_node timed out after %ds for job %s", _TIMEOUT, state.job_id)
        return {
            "status": JobStatus.PAUSED,
            "paused_at_node": "plan",
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"plan_node timed out after {_TIMEOUT}s"],
        }
    except Exception as exc:
        logger.exception("plan_node failed for job %s: %s", state.job_id, exc)
        return {
            "status": JobStatus.FAILED,
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"plan_node error: {exc}"],
        }

    plan = ImplementationPlan(
        requirements_brief=result.requirements_brief,
        approach=result.approach,
        files=result.files,
        estimated_input_tokens=result.estimated_input_tokens,
        estimated_output_tokens=result.estimated_output_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
        status="pending",
    )

    logger.info(
        "plan_node job %s: plan ready — %d files, est. $%.4f",
        state.job_id, len(plan.files), plan.estimated_cost_usd,
    )

    return {
        "implementation_plan": plan,
        "status": JobStatus.PLANNING,
        "provider_log": state.provider_log + [f"plan:{provider_label}"],
        "updated_at": datetime.utcnow(),
        "_input_tokens": handler.input_tokens,
        "_output_tokens": handler.output_tokens,
    }
