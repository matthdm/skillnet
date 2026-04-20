from __future__ import annotations

import json
import logging
import time


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{record.msecs:03.0f}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if (job_id := getattr(record, "job_id", None)):
            payload["job_id"] = job_id
        if (node := getattr(record, "node", None)):
            payload["node"] = node
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=[handler])
    for noisy in ("httpx", "httpcore", "chromadb", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
