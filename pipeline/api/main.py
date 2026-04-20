# CODEX TASK C4 â€” implement this file exactly as specified
# FastAPI app entry point. Wire routes, startup events, and health check.
# Do not add business logic here â€” only routing and app configuration.

from __future__ import annotations

from fastapi import FastAPI

from api.routes import ingest, jobs

app = FastAPI(title="Skillnet Pipeline", version="0.1.0")

app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])


@app.on_event("startup")
async def on_startup() -> None:
    app.state.ready = True


@app.on_event("shutdown")
async def on_shutdown() -> None:
    app.state.ready = False


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
