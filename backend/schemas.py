"""Pydantic request/response schemas."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Generous-but-not-infinite caps to stop runaway inputs from swamping the
# database or bloating an agent's context window. Calibrated so legitimate
# human input (names, descriptions, pasted code snippets, long chat turns)
# still fits comfortably.
_NAME_MAX = 255
_DESC_MAX = 10_000
_INSTRUCTIONS_MAX = 50_000
_MESSAGE_MAX = 500_000
_CLARIFICATION_MAX = 10_000
_MODEL_ID_MAX = 100
_UUID_MAX = 64
_GPU_MAX = 32


class SandboxProfile(BaseModel):
    gpu: Optional[str] = Field(default=None, max_length=_GPU_MAX)
    timeout: Optional[int] = Field(default=None, ge=10, le=7200)


class SandboxConfig(BaseModel):
    default: Optional[SandboxProfile] = None
    training: Optional[SandboxProfile] = None


class ExperimentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=_NAME_MAX)
    description: str = Field(default="", max_length=_DESC_MAX)
    instructions: str = Field(default="", max_length=_INSTRUCTIONS_MAX)


class Mention(BaseModel):
    kind: Literal["file", "session"]
    ref: str = Field(..., max_length=2048)
    label: str = Field(..., max_length=_NAME_MAX)
    sandbox_path: Optional[str] = Field(default=None, max_length=2048)
    experiment_id: Optional[str] = Field(default=None, max_length=_UUID_MAX)


class MessageCreate(BaseModel):
    content: str = Field(..., max_length=_MESSAGE_MAX)
    run_agent: bool = False
    model: Optional[str] = Field(default=None, max_length=_MODEL_ID_MAX)
    # Per-agent model overrides: {"eda": "claude-haiku-4-5", "trainer": "claude-opus-4-6"}
    agent_models: Optional[dict[str, str]] = Field(default=None)
    # Per-agent reasoning level overrides: {"eda": "high", "trainer": "off"}.
    # Levels: "off" | "low" | "medium" | "high". Translated per-provider in
    # services/llm/thinking.py. Ignored for models that don't support it.
    agent_thinking: Optional[dict[str, str]] = Field(default=None)
    mentions: Optional[list[Mention]] = Field(default=None, max_length=64)


class ClarificationReply(BaseModel):
    answer: str = Field(..., max_length=_CLARIFICATION_MAX)


class TaskCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=_NAME_MAX)
    short_description: str = Field(default="", max_length=_DESC_MAX)
    description: str = Field(default="", max_length=_DESC_MAX)
    active_form: Optional[str] = Field(default=None, max_length=_NAME_MAX)
    status: Literal["pending", "in_progress", "completed"] = "pending"


class TaskUpdate(BaseModel):
    subject: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    short_description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    active_form: Optional[str] = Field(default=None, max_length=_NAME_MAX)
    status: Optional[Literal["pending", "in_progress", "completed"]] = None


class ProjectCreate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    sandbox_config: Optional[SandboxConfig] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    sandbox_config: Optional[SandboxConfig] = None


class ReproduceRequest(BaseModel):
    """Optional knobs for the snapshot reproduce action."""

    # Relative+absolute tolerance for "same metric value" (see
    # services/reproduce.py). Widen for intentionally-stochastic runs.
    tolerance: float = Field(default=1e-6, ge=0.0, le=1.0)


class ChangedFile(BaseModel):
    path: str
    expected_sha256: str
    actual_sha256: Optional[str] = None  # None => file no longer exists


class ReproduceInputs(BaseModel):
    dataset_verified: bool
    code_verified: bool
    changed_files: list[ChangedFile]


class ReproduceExecution(BaseModel):
    returncode: int
    scripts: list[str]
    stderr_tail: str


class MetricDiffRow(BaseModel):
    name: str
    original: Optional[float] = None
    reproduced: Optional[float] = None
    abs_diff: Optional[float] = None
    rel_diff: Optional[float] = None
    status: Literal["match", "drift", "missing", "new"]


class MetricDiffSummary(BaseModel):
    matched: int
    drifted: int
    missing: int
    new: int


class ReproduceMetrics(BaseModel):
    original: dict[str, float]
    reproduced: dict[str, float]
    rows: list[MetricDiffRow]
    summary: MetricDiffSummary
    drift_detected: bool


class ReproduceReport(BaseModel):
    session_id: str
    snapshot_id: int
    reproduced_at: str
    tolerance: float
    status: Literal["match", "drift", "error"]
    inputs: ReproduceInputs
    execution: ReproduceExecution
    metrics: ReproduceMetrics


class ExperimentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    project_id: Optional[str] = Field(default=None, max_length=_UUID_MAX)
    instructions: Optional[str] = Field(default=None, max_length=_INSTRUCTIONS_MAX)
    tags: Optional[list[str]] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
