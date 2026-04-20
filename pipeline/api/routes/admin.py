from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from scripts.ingest_skills import load_all_skills

logger = logging.getLogger(__name__)
router = APIRouter()


@dataclass
class IngestionStatus:
    state: str = "idle"          # "idle" | "running" | "complete" | "failed"
    skills_processed: int = 0
    skills_total: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "skills_processed": self.skills_processed,
            "skills_total": self.skills_total,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }


_status = IngestionStatus()


@router.post("/ingest-skills", status_code=202)
async def trigger_ingestion(request: Request) -> dict:
    if _status.state == "running":
        raise HTTPException(status_code=409, detail="Ingestion already running.")

    store = request.app.state.store
    _status.state = "running"
    _status.skills_processed = 0
    _status.skills_total = 0
    _status.started_at = datetime.utcnow()
    _status.completed_at = None
    _status.error = None

    asyncio.create_task(_run_ingestion(store))
    logger.info("Skill ingestion triggered via admin endpoint.")
    return {"status": "started"}


@router.get("/ingest-skills/status")
async def ingestion_status() -> dict:
    return _status.to_dict()


async def _run_ingestion(store) -> None:
    try:
        skill_dicts = await asyncio.to_thread(load_all_skills)
        _status.skills_total = len(skill_dicts)
        logger.info("Ingestion: loaded %d skills, starting embedding.", _status.skills_total)

        def _on_batch(processed: int, total: int) -> None:
            _status.skills_processed = processed
            logger.info("Ingestion progress: %d / %d", processed, total)

        await asyncio.to_thread(store.embed_and_upsert, skill_dicts, _on_batch)

        _status.state = "complete"
        _status.skills_processed = _status.skills_total
        _status.completed_at = datetime.utcnow()
        logger.info("Ingestion complete: %d skills indexed.", _status.skills_total)

    except Exception as exc:
        _status.state = "failed"
        _status.error = str(exc)
        _status.completed_at = datetime.utcnow()
        logger.exception("Ingestion failed: %s", exc)
