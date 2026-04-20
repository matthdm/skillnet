from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile

from datetime import datetime
from pathlib import Path

from models.job import JobState, JobStatus
from models.skill import TestResult

logger = logging.getLogger(__name__)

_TIMEOUT = 30  # seconds

_PASS_RE = re.compile(r"(\d+) passed")
_FAIL_RE = re.compile(r"(\d+) failed")


def _parse_output(output: str) -> TestResult:
    pass_count = int(m.group(1)) if (m := _PASS_RE.search(output)) else 0
    fail_count = int(m.group(1)) if (m := _FAIL_RE.search(output)) else 0
    failures = [
        line.strip()
        for line in output.splitlines()
        if line.startswith("FAILED")
    ]
    passed = fail_count == 0 and pass_count > 0
    return TestResult(
        passed=passed,
        pass_count=pass_count,
        fail_count=fail_count,
        failures=failures,
        raw_output=output[-4000:],  # keep tail for interpret context
    )


async def test_node(state: JobState) -> dict:
    if not state.generated_files:
        return {
            "status": JobStatus.FAILED,
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + ["test_node: no generated files to test"],
        }

    output: str = ""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for rel_path, content in state.generated_files.items():
                full_path = Path(tmpdir) / rel_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")

            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "pytest", "-v", "--tb=short", tmpdir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ, "PYTHONPATH": tmpdir},
                ),
                timeout=_TIMEOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
            output = stdout.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        logger.warning("test_node timed out after %ds for job %s", _TIMEOUT, state.job_id)
        return {
            "status": JobStatus.PAUSED,
            "paused_at_node": "test",
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"test_node timed out after {_TIMEOUT}s"],
        }
    except FileNotFoundError:
        logger.error("pytest not found in PATH for job %s", state.job_id)
        return {
            "status": JobStatus.FAILED,
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + ["test_node: pytest not found in PATH"],
        }
    except Exception as exc:
        logger.exception("test_node failed for job %s: %s", state.job_id, exc)
        return {
            "status": JobStatus.FAILED,
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs + [f"test_node error: {exc}"],
        }

    result = _parse_output(output)
    logger.info(
        "test_node job %s: %d passed, %d failed",
        state.job_id, result.pass_count, result.fail_count,
    )

    return {
        "test_results": result,
        "status": JobStatus.TESTING,
        "updated_at": datetime.utcnow(),
    }
