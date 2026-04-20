# CODEX TASK C1 — implement this file exactly as specified
# Field names and types must match DESIGN.md Section 6 exactly. Do not rename or restructure.

from __future__ import annotations

from pydantic import BaseModel, Field


class Skill(BaseModel):
    skill_id: str
    name: str
    description: str
    category: str = ""
    tags: list[str]
    body: str
    embedding: list[float]
    source_path: str
    supports_codex: bool = True
    supports_claude: bool = True


class SkillMatch(BaseModel):
    skill: Skill
    score: float


class TestResult(BaseModel):
    passed: bool
    pass_count: int
    fail_count: int
    failures: list[str]
    raw_output: str


class FeatureSpec(BaseModel):
    feature_id: str
    title: str
    description: str
    acceptance_criteria: str = ""
    tech_stack_hint: list[str] = Field(default_factory=list)
    job_type: str = "feature"
    project_id: str | None = None
    target_repo: str | None = None
    repo_name: str | None = None         # explicit override; parsed from ## Repo Name section
    source: str = "markdown"             # "markdown" | "rally" | "manual"
