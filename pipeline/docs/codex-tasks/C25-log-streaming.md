# Codex Task C25 — Log Streaming (Verify & Polish)

## Status
Implemented by Claude. Codex to verify, test, and polish.

## What Was Built
A live log streaming system so the dashboard can display pipeline worker logs in real time.

### Files Changed / Created

**`pipeline/logging_config.py`** — added `_BufferHandler` and global `log_buffer`
- `log_buffer: collections.deque[dict]` with `maxlen=500`
- `_BufferHandler` appends structured dicts (ts, level, logger, msg, job_id, node) to the deque on every log emit
- Both the existing `StreamHandler` (stdout) and the new `_BufferHandler` are registered in `setup_logging()`

**`pipeline/api/routes/admin.py`** — added `GET /admin/logs`
```python
@router.get("/logs")
async def get_logs(n: int = 200, level: str = "", job_id: str = "") -> dict:
    from logging_config import log_buffer
    lines = list(log_buffer)
    if level:
        lines = [l for l in lines if l.get("level", "").upper() == level.upper()]
    if job_id:
        lines = [l for l in lines if l.get("job_id") == job_id]
    return {"lines": lines[-n:], "total": len(lines)}
```

**`pipeline/dashboard/pages/3_Logs.py`** — new Streamlit page
- Filters: level dropdown, job ID text input, line count selector
- Displays logs newest-first with color-coded level badges
- Auto-refreshes every 3 seconds via `time.sleep(3) + st.rerun()`
- Shows job_id (first 8 chars) and node name as inline tags

## Codex Verification Tasks

1. **Smoke test the endpoint**: `curl http://localhost:8000/admin/logs?n=50` should return JSON with a `lines` array.
2. **Verify filters work**: `?level=ERROR` should return only error lines; `?job_id=<uuid>` should filter by job.
3. **Check the Streamlit page loads** at http://localhost:8501 under "3 Logs" without errors.
4. **Verify no duplicate handler registration**: If `setup_logging()` is called twice (e.g., during uvicorn `--reload`), the buffer handler should not be added twice. Add a guard if needed:
   ```python
   root = logging.getLogger()
   if not any(isinstance(h, _BufferHandler) for h in root.handlers):
       root.addHandler(buf_handler)
   ```
5. **Thread safety**: `collections.deque` with `maxlen` is thread-safe for single append/iterate operations in CPython — no lock needed. Confirm this assumption holds with uvicorn's default single-worker async mode.

## Known Limitations
- Buffer is in-memory only — cleared on container restart.
- Noisy loggers (httpx, httpcore, chromadb, hpack) are suppressed at WARNING+ so they won't appear.
- The Streamlit page uses `time.sleep(3)` which blocks the session thread. Future improvement: use `streamlit-autorefresh` component for non-blocking refresh.
