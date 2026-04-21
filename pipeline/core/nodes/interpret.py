from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from core.token_tracker import extract_usage
from models.job import JobState, JobStatus

logger = logging.getLogger(__name__)

_TIMEOUT = 120  # seconds
_MAX_ERROR_CHARS = 2000
_MAX_FILE_CHARS = 3000

_SYSTEM = """You are a debugging expert specializing in root-cause analysis for automated test \
failures. Your role is to synthesize error logs and code states into actionable fix instructions.

Output ONLY valid JSON. No markdown wrappers, no conversational text.
Ensure the following keys are present:
- root_cause: A concise explanation of WHY the test failed (e.g. "Type mismatch in function argument").
- fix_instructions: Specific, copy-pasteable instructions or code blocks to apply the fix.
- files_to_modify: A list of relative file paths that require changes."""

_USER_TEMPLATE = """Analyze the test failures and provide a fix plan in JSON format.

Failing Tests:
{failing_tests}

Error Output (truncated to last {error_chars} chars):
{test_output}

File Contents Involved:
{failing_files}

Determine the root cause, identify which files need modification, and provide specific fix \
instructions. Return ONLY valid JSON with keys: root_cause, fix_instructions, files_to_modify."""


class InterpretResult(BaseModel):
    root_cause: str = ""
    fix_instructions: str = ""
    files_to_modify: list[str] = Field(default_factory=list)


async def interpret_failure_node(state: JobState, llm: BaseChatModel, provider_label: str) -> dict:
    test_results = state.test_results
    if test_results is None:
        return {
            "status": JobStatus.FAILED,
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + ["interpret_node: no test_results to interpret"],
        }

    raw_output = test_results.raw_output[-_MAX_ERROR_CHARS:]

    failing_names = {
        f.split("::")[0].replace("/", ".").replace("\\", ".")
        for f in test_results.failures
    }
    failing_files_text = "\n\n".join(
        f"### {path}\n```python\n{content[:_MAX_FILE_CHARS]}\n```"
        for path, content in state.generated_files.items()
        if any(part in path for part in failing_names) or not failing_names
    ) or "(no generated files)"

    failing_tests_text = "\n".join(test_results.failures) or "(no individual test names captured)"

    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=_USER_TEMPLATE.format(
            failing_tests=failing_tests_text,
            error_chars=_MAX_ERROR_CHARS,
            test_output=raw_output,
            failing_files=failing_files_text,
        )),
    ]

    try:
        structured_llm = llm.with_structured_output(InterpretResult, include_raw=True)
        raw = await asyncio.wait_for(
            structured_llm.ainvoke(messages),
            timeout=_TIMEOUT,
        )
        result: InterpretResult = raw["parsed"]
        input_tokens, output_tokens = extract_usage(raw.get("raw"))
    except asyncio.TimeoutError:
        logger.warning("interpret_node timed out after %ds for job %s", _TIMEOUT, state.job_id)
        return {
            "status": JobStatus.PAUSED,
            "paused_at_node": "interpret",
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"interpret_node timed out after {_TIMEOUT}s"],
        }
    except Exception as exc:
        logger.exception("interpret_node failed for job %s: %s", state.job_id, exc)
        return {
            "status": JobStatus.FAILED,
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"interpret_node error: {exc}"],
        }

    updated_story = {
        **state.story_content,
        "interpretation": result.model_dump(),
    }

    return {
        "story_content": updated_story,
        "error_logs": state.error_logs + [
            f"interpret[iter={state.iteration_count}]: {result.root_cause}"
        ],
        "provider_log": state.provider_log + [f"interpret:{provider_label}"],
        "updated_at": datetime.utcnow(),
        "_input_tokens": input_tokens,
        "_output_tokens": output_tokens,
    }
