from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Sensitivity = Literal["low", "medium", "high"]
ACL = Literal["private", "team", "org"]
SortMode = Literal["score", "similarity", "q_value", "recency"]


class TrajectoryStep(BaseModel):
    role: str
    content: str
    tool: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None


class PushRequest(BaseModel):
    task_type: str
    source_model: str
    trajectory: list[TrajectoryStep]
    objective_signals: dict[str, Any] = Field(default_factory=dict)
    parent_experience_ids: list[UUID] = Field(default_factory=list)
    sensitivity: Sensitivity = "medium"
    acl: str = "private"  # "private" | "team:<dept>" | "org"
    tags: list[str] = Field(default_factory=list)


class PushResponse(BaseModel):
    experience_id: UUID
    status: str
    review_required: bool


class ScoreComponents(BaseModel):
    similarity: float
    q_value: float
    exploration: float


class SearchHit(BaseModel):
    experience_id: UUID
    intent_text: str | None
    script: dict[str, Any] | None
    summary: str | None
    q_scalar: float
    q_breakdown: dict[str, float]
    visit_count: int
    reuse_count: int
    score: float
    score_components: ScoreComponents


class SearchResponse(BaseModel):
    results: list[SearchHit]


class RegisterRequest(BaseModel):
    name: str
    team: str


class RegisterResponse(BaseModel):
    agent_id: UUID
    name: str
    team: str
