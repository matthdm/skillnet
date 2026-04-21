# Task C26 — Docker Build & Smoke Test Node (Claude)

> **DESIGN.md reference:** Section roadmap line 29 ("Docker-in-Docker sub-agent sandboxing")
> and the deferred features table (line 735). This was planned from v1 — now implementing.

## Goal
After codegen succeeds for `new_service` jobs, verify the generated code actually builds and
runs before committing. Add a `build_node` to the pipeline that performs:
1. `docker build` the generated Dockerfile
2. `docker run` the container
3. Hit the health endpoint to confirm the service starts
4. Tear down the container and image
5. Report pass/fail back to the graph

## Why
The existing `test_node` runs `pytest` against generated unit tests, but this doesn't prove
the service actually starts. A broken import, missing env var, or port conflict will only
surface at runtime. The build node closes this gap for `new_service` jobs.

## Prerequisites
Mount the Docker socket into the API container so it can spawn sibling containers:
```yaml
# pipeline/docker-compose.yml — api service
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```
Also add `docker` CLI to `Dockerfile.api`:
```dockerfile
RUN apt-get update && apt-get install -y docker.io && rm -rf /var/lib/apt/lists/*
```

## Implementation

### New file: `pipeline/core/nodes/build.py`

```python
from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime
from pathlib import Path

from models.job import JobState, JobStatus

logger = logging.getLogger(__name__)

_BUILD_TIMEOUT = 120   # seconds — docker build
_RUN_TIMEOUT   = 30    # seconds — container start + health check
_HEALTH_RETRIES = 10
_HEALTH_INTERVAL = 2   # seconds between retries


async def build_node(state: JobState) -> dict:
    """Only runs for new_service jobs that have a Dockerfile."""
    if state.job_type != "new_service":
        return {}  # no-op for other job types

    if "Dockerfile" not in state.generated_files:
        logger.warning("build_node: no Dockerfile in generated files for job %s", state.job_id)
        return {}

    image_tag = f"skillnet-job-{state.job_id[:8]}"
    container_name = f"skillnet-smoke-{state.job_id[:8]}"

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write all generated files to temp dir
        for rel_path, content in state.generated_files.items():
            full = Path(tmpdir) / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        # --- docker build ---
        try:
            build_proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "docker", "build", "-t", image_tag, ".",
                    cwd=tmpdir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                ),
                timeout=_BUILD_TIMEOUT,
            )
            build_out, _ = await asyncio.wait_for(
                build_proc.communicate(), timeout=_BUILD_TIMEOUT
            )
            build_log = build_out.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            return _fail(state, f"build_node: docker build timed out after {_BUILD_TIMEOUT}s")
        except Exception as exc:
            return _fail(state, f"build_node: docker build error: {exc}")

        if build_proc.returncode != 0:
            snippet = build_log[-1000:]
            return _fail(state, f"build_node: docker build failed:\n{snippet}")

        logger.info("build_node: image %s built successfully for job %s", image_tag, state.job_id)

        # --- docker run ---
        # Detect port from generated config.py or default to 8090
        port = _detect_port(state.generated_files)
        host_port = _find_free_port()

        try:
            run_proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "docker", "run", "-d",
                    "--name", container_name,
                    "-p", f"{host_port}:{port}",
                    image_tag,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                ),
                timeout=15,
            )
            run_out, _ = await asyncio.wait_for(run_proc.communicate(), timeout=15)
        except Exception as exc:
            await _cleanup(image_tag, container_name)
            return _fail(state, f"build_node: docker run error: {exc}")

        if run_proc.returncode != 0:
            await _cleanup(image_tag, container_name)
            return _fail(state, f"build_node: docker run failed: {run_out.decode()[-500:]}")

        # --- health check ---
        import httpx
        health_ok = False
        for _ in range(_HEALTH_RETRIES):
            await asyncio.sleep(_HEALTH_INTERVAL)
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(f"http://localhost:{host_port}/health", timeout=3)
                    if r.status_code == 200:
                        health_ok = True
                        break
            except Exception:
                pass

        await _cleanup(image_tag, container_name)

        if not health_ok:
            return _fail(state, f"build_node: service did not respond to /health on port {host_port}")

        logger.info("build_node: smoke test passed for job %s", state.job_id)
        return {
            "updated_at": datetime.utcnow(),
            "error_logs": state.error_logs,  # unchanged — build passed
        }


def _detect_port(files: dict[str, str]) -> int:
    for content in files.values():
        for line in content.splitlines():
            if "port" in line.lower() and "8090" in line:
                return 8090
            if "port" in line.lower() and "8000" in line:
                return 8000
    return 8090


def _find_free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def _cleanup(image_tag: str, container_name: str) -> None:
    for cmd in [
        ["docker", "stop", container_name],
        ["docker", "rm", container_name],
        ["docker", "rmi", image_tag],
    ]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
        except Exception:
            pass


def _fail(state: JobState, msg: str) -> dict:
    logger.warning(msg)
    return {
        "status": JobStatus.FAILED,
        "updated_at": datetime.utcnow(),
        "error_logs": state.error_logs + [msg],
    }
```

### Graph wiring — `pipeline/core/graph.py`

Add `build_node` between `codegen` and `test`:

```python
from core.nodes.build import build_node

workflow.add_node("build", build_node)

# Replace direct codegen→test edge:
workflow.add_conditional_edges(
    "codegen",
    _route_after_codegen,
    {"build": "build", END: END},   # was {"test": "test", END: END}
)
workflow.add_conditional_edges(
    "build",
    _route_after_build,
    {"test": "test", END: END},
)
```

Add routing function:
```python
def _route_after_codegen(state: JobState) -> str:
    if state.status in _HALT_STATUSES:
        return END
    return "build"   # always go to build; build_node no-ops for non-new_service

def _route_after_build(state: JobState) -> str:
    if state.status in _HALT_STATUSES:
        return END
    return "test"
```

### Dashboard pipeline stages
Add `"build"` to `PIPELINE_STAGES` in `1_Jobs_Queue.py` and `2_Job_Detail.py`:
```python
PIPELINE_STAGES = [
    "pending", "injected", "analyzed", "skills_retrieved",
    "coding", "building", "testing", "committed",
]
```
Add `JobStatus.BUILDING = "building"` to `models/job.py` and return it from `build_node`
on success.

## Scope
- `new_service` jobs only — skip entirely for `feature` and `change_request`
- Failure in build_node sets status FAILED (same as test failure — triggers interpret loop)
- No build caching between jobs — each run gets a fresh image
- Docker socket mount is a security consideration: only acceptable because the API runs
  in a trusted local/internal environment

## Files to Create / Modify
| File | Action |
|------|--------|
| `pipeline/core/nodes/build.py` | Create |
| `pipeline/core/graph.py` | Modify — add build node and edges |
| `pipeline/models/job.py` | Modify — add `BUILDING` status |
| `pipeline/docker-compose.yml` | Modify — mount Docker socket |
| `pipeline/Dockerfile.api` | Modify — install docker CLI |
| `pipeline/dashboard/pages/1_Jobs_Queue.py` | Modify — add "building" stage |
| `pipeline/dashboard/pages/2_Job_Detail.py` | Modify — add "building" stage + color |
