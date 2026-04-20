# CODEX TASK C1 — implement this file exactly as specified
# Field names and types must match DESIGN.md Section 6 exactly. Do not rename or restructure.
# Imports required: pydantic, datetime, enum
# Do not add fields not listed here. Do not add validators.

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from .skill import SkillMatch, TestResult


class PlanFile(BaseModel):
    path: str
    action: str                        # "create" | "modify" | "delete"
    description: str                   # ≤ 20 words: what this file does


class ImplementationPlan(BaseModel):
    requirements_brief: str            # ≤ 100 words: LLM's understanding of what must be built
    approach: str                      # ≤ 150 words: how it will be built
    files: list[PlanFile]
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    status: str = "pending"            # "pending" | "approved" | "rejected"
    rejection_reason: str | None = None


class NodeTrace(BaseModel):
    node: str
    status_after: str  # JobStatus value — stored as string to avoid forward-ref issues
    provider: str | None = None
    duration_ms: int
    iteration: int
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class JobStatus(str, Enum):
    PENDING = "pending"
    INJECTED = "injected"
    ANALYZED = "analyzed"
    SKILLS_RETRIEVED = "skills_retrieved"
    CODING = "coding"
    TESTING = "testing"
    COMMITTED = "committed"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    PAUSED = "paused"
    PLANNING = "planning"
    REJECTED = "rejected"


class JobState(BaseModel):
    job_id: str
    story_id: str
    story_content: dict
    tech_stack: list[str] = Field(default_factory=list)
    skills_pool: list[SkillMatch] = Field(default_factory=list)
    repo_name: str = ""
    repo_url: str | None = None
    generated_files: dict[str, str] = Field(default_factory=dict)
    test_results: TestResult | None = None
    iteration_count: int = 0
    max_iterations: int = 3
    error_logs: list[str] = Field(default_factory=list)
    status: JobStatus = JobStatus.PENDING
    provider_log: list[str] = Field(default_factory=list)
    degraded: bool = False
    degraded_nodes: list[str] = Field(default_factory=list)
    repair_mode: bool = False
    last_commit_hash: str | None = None
    pr_url: str | None = None
    paused_at_node: str | None = None
    execution_trace: list[NodeTrace] = Field(default_factory=list)
    implementation_plan: ImplementationPlan | None = None
    # submission context — set at ingest, read-only thereafter
    job_type: str = "feature"              # "feature" | "new_service" | "change_request"
    project_id: str | None = None          # links job to a Project
    parent_job_id: str | None = None       # set when this job is a retry/resume of a prior job
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
