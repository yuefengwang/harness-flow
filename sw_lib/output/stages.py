"""Stage-specific output schemas for structured agent output validation."""

from typing import List, Optional
from pydantic import BaseModel, Field

from .schema import StageOutputSchema


# ── 01-Brainstorming ──

class DecisionPoint(BaseModel):
    topic: str = Field(description="Decision topic")
    chosen: str = Field(description="Chosen option label")
    rationale: str = Field(default="", description="Why this option was chosen")


class BrainstormingOutput(StageOutputSchema):
    stage: str = "01-brainstorming"
    ambiguity_score: int = Field(ge=0, le=10, default=0,
                                  description="Ambiguity score 0-10")
    goal: str = Field(default="", description="One-line project goal")
    decisions: List[DecisionPoint] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list, description="Identified risks")


# ── 02-Planning ──

class TaskItem(BaseModel):
    id: int
    name: str = Field(description="Task name")
    deps: List[int] = Field(default_factory=list)
    description: str = ""
    verify_cmd: str = ""


class PlanningOutput(StageOutputSchema):
    stage: str = "02-planning"
    tasks: List[TaskItem] = Field(default_factory=list, description="Task breakdown")
    tech_stack: str = ""
    test_strategy: str = ""
    key_files: List[str] = Field(default_factory=list)


# ── 03-Coding ──

class CodingOutput(StageOutputSchema):
    stage: str = "03-coding"
    pattern: str = Field(default="", description="Design pattern used")
    decisions: str = Field(default="", description="Key technical decisions")
    build_passes: bool = False
    test_passes: bool = False
    files_changed: List[str] = Field(default_factory=list)


# ── 04-Review ──

class ReviewFinding(BaseModel):
    id: int
    severity: str = Field(default="medium", pattern="^(high|medium|low)$")
    category: str = ""
    location: str = ""
    description: str = ""


class ReviewOutput(StageOutputSchema):
    stage: str = "04-review"
    passed: bool = Field(default=False, description="Overall review passed")
    route: str = Field(default="05-Archive",
                        description="Routing: 05-Archive | 03-Coding | 02-Planning | 01-Brainstorming")
    findings: List[ReviewFinding] = Field(default_factory=list)
    reason: str = ""


# ── 05-Archive ──

class ArchiveOutput(StageOutputSchema):
    stage: str = "05-archive"
    summary: str = ""
    learnings: str = ""
    patterns_to_promote: List[str] = Field(default_factory=list)
