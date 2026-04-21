from __future__ import annotations

import collections
import json
import logging
import os

# Global ring buffer — last 500 lines, for the global /admin/logs endpoint
log_buffer: collections.deque[dict] = collections.deque(maxlen=500)

_redis_client = None


def _get_sync_redis():
    global _redis_client
    if _redis_client is None:
        try:
            from redis import Redis
            _redis_client = Redis.from_url(
                os.environ.get("REDIS_URL", "redis://localhost:6379"),
                decode_responses=True,
                socket_connect_timeout=1,
            )
        except Exception:
            pass
    return _redis_client


def _build_payload(record: logging.LogRecord, formatter: logging.Formatter) -> dict:
    payload: dict = {
        "ts": formatter.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{record.msecs:03.0f}Z",
        "level": record.levelname,
        "logger": record.name,
        "msg": record.getMessage(),
    }
    if (job_id := getattr(record, "job_id", None)):
        payload["job_id"] = job_id
    if (node := getattr(record, "node", None)):
        payload["node"] = node
    if record.exc_info:
        payload["exc"] = formatter.formatException(record.exc_info)
    return payload


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_build_payload(record, self))


class _BufferHandler(logging.Handler):
    """Writes to in-memory ring buffer and to per-job Redis list."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = _build_payload(record, self.formatter)
            log_buffer.append(payload)

            # Per-job persistence — survives restarts, readable for completed jobs
            job_id = payload.get("job_id")
            if job_id:
                r = _get_sync_redis()
                if r:
                    key = f"job:{job_id}:logs"
                    r.rpush(key, json.dumps(payload))
                    r.ltrim(key, -500, -1)   # keep last 500 per job
                    r.expire(key, 604800)    # 7-day TTL
        except Exception:
            pass


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    # Guard against double-registration on uvicorn --reload
    if any(isinstance(h, _BufferHandler) for h in root.handlers):
        return
    fmt = _JsonFormatter()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    buf_handler = _BufferHandler()
    buf_handler.setFormatter(fmt)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[stream_handler, buf_handler],
    )
    for noisy in ("httpx", "httpcore", "chromadb", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
