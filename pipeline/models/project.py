from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Project(BaseModel):
    project_id: str
    name: str
    description: str = ""
    repo_url: str | None = None
    feature_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
