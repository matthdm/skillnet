# FEAT-005: In-Memory Task Tracker Service

## Description

Build a complete, runnable REST API service for managing tasks.
The service stores tasks in memory (no external database).
It must be deployable as a standalone container — include a Dockerfile, a configuration
module, and a README with setup instructions.
No external dependencies beyond FastAPI and Pydantic.

## Acceptance Criteria

- `POST /tasks` accepts `{title: str, description: str}`, returns the created task with a generated `id` (UUID) and `status: "pending"`.
- `GET /tasks` returns all tasks as a list, optionally filtered by `?status=pending|in_progress|done`.
- `GET /tasks/{id}` returns a single task by ID. Returns 404 if not found.
- `PATCH /tasks/{id}` accepts `{status: str}` and updates the task status. Valid values: `pending`, `in_progress`, `done`. Returns 422 on invalid status. Returns 404 if not found.
- `DELETE /tasks/{id}` removes the task. Returns 404 if not found. Returns 204 on success.
- `GET /health` returns `{"status": "ok", "task_count": N}`.
- All task IDs are UUIDs generated at creation time.
- Concurrent reads and writes are safe — the in-memory store must use a lock.
- The service exposes port 8080.
- A `Dockerfile` builds and runs the service (`CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]`).
- A `config.py` module exposes `Settings` (Pydantic BaseSettings) with at least `app_title: str` and `max_tasks: int` (default 1000).
- A `README.md` documents: how to build the Docker image, how to run it, and the full API surface.
- Unit tests cover: create task, list with filter, get by ID (found + not found), update status (valid + invalid), delete (found + not found), health endpoint.

## Tech Stack

- Python
- FastAPI
- Pydantic
- uvicorn
- threading
- uuid
