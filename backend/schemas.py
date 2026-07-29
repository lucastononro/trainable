"""Pydantic request/response schemas."""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

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


# Model families a user can restrict the trainer to. Mirrors the `framework`
# vocabulary of the start-training skill (see skills/start-training/schema.yaml).
KNOWN_MODEL_FAMILIES = (
    "xgboost",
    "lightgbm",
    "sklearn",
    "pytorch",
    "tensorflow",
    "huggingface",
    "other",
)

# Metric names are rendered verbatim into agent system prompts (inside a
# backtick fence) — restrict to a plain-identifier charset so a crafted value
# can't break out of the fence or smuggle prompt directives.
_METRIC_RE = re.compile(r"^[A-Za-z0-9 _\-./@:()]+$")


class TrainingConfig(BaseModel):
    """Pre-flight training controls (issue #104).

    Everything is optional — an empty config means the trainer agent keeps
    full autonomy (today's behavior). Any field the user sets becomes a
    constraint the orchestrator/trainer must honor: it is injected into the
    agent system prompt and enforced at the start-training skill boundary.
    """

    # Metric the tuning loop must optimize (e.g. "roc_auc", "pr_auc", "f1", "rmse").
    optimization_metric: Optional[str] = Field(default=None, max_length=64)
    # Allowed model families. Empty/None = agent's choice.
    model_families: Optional[list[str]] = Field(default=None, max_length=16)
    # Hard cap on hyperparameter-search trials (Optuna or equivalent).
    max_trials: Optional[int] = Field(default=None, ge=1, le=1000)
    # Wall-clock budget for training work, in minutes. Also clamps the
    # training sandbox profile's per-call timeout.
    max_wallclock_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    # Advisory spend cap for the training run, in USD.
    max_cost_usd: Optional[float] = Field(default=None, gt=0, le=100_000)

    @field_validator("optimization_metric")
    @classmethod
    def _clean_metric(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if not v:
            return None
        if not _METRIC_RE.fullmatch(v):
            raise ValueError(
                "optimization_metric may only contain letters, digits, spaces "
                "and _-./@:() characters"
            )
        return v

    @field_validator("model_families")
    @classmethod
    def _clean_families(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        cleaned: list[str] = []
        for fam in v:
            fam = (fam or "").strip().lower()
            if not fam:
                continue
            if fam not in KNOWN_MODEL_FAMILIES:
                raise ValueError(
                    f"Unknown model family '{fam}'. "
                    f"Valid: {', '.join(KNOWN_MODEL_FAMILIES)}"
                )
            if fam not in cleaned:
                cleaned.append(fam)
        return cleaned or None


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


class SessionResume(BaseModel):
    """Body for POST /sessions/{id}/resume. Everything is optional — the
    default is 'relaunch with the same knobs the session already has'."""

    # "resume" continues interrupted work; "retry" re-drives a failed stage.
    # Both relaunch the same way — the mode only shades the agent prompt.
    mode: Literal["resume", "retry"] = "resume"
    model: Optional[str] = Field(default=None, max_length=_MODEL_ID_MAX)
    agent_models: Optional[dict[str, str]] = Field(default=None)
    agent_thinking: Optional[dict[str, str]] = Field(default=None)


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
    # Hard-stop USD spend cap for the project. None = uncapped.
    budget_usd: Optional[float] = Field(default=None, ge=0.0, le=1_000_000)
    training_config: Optional[TrainingConfig] = None


class ProjectFromSample(BaseModel):
    """POST /projects/from-sample — one-click sample-dataset project."""

    sample_id: str = Field(min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, max_length=_NAME_MAX)


class SampleDatasetEntry(BaseModel):
    """One tile of the sample-dataset gallery (GET /samples)."""

    id: str
    name: str
    task: str
    description: str
    suggested_prompt: str
    file_count: int = Field(ge=0)
    size_bytes: int = Field(ge=0)
    # False when the sample's data files aren't shipped in this deployment.
    available: bool


class SampleProjectSummary(BaseModel):
    """Project as returned by POST /projects/from-sample (Project.to_dict)."""

    id: str
    name: str
    description: str
    sandbox_config: dict[str, Any]
    created_at: str
    updated_at: str
    experiment_count: int = Field(ge=0)
    dataset_count: int = Field(ge=0)
    model_count: int = Field(ge=0)


class SampleExperimentSummary(BaseModel):
    """Initial experiment created alongside a from-sample project."""

    id: str
    project_id: str
    name: str
    description: str
    dataset_ref: str
    instructions: str
    created_at: str
    updated_at: str
    latest_session_id: str
    latest_state: str


class ProjectFromSampleResponse(BaseModel):
    """POST /projects/from-sample response."""

    project: SampleProjectSummary
    experiment: SampleExperimentSummary
    session_id: str
    sample_id: str
    suggested_prompt: str
    uploaded_files: list[str]


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    sandbox_config: Optional[SandboxConfig] = None
    # PATCH semantics: omit to leave unchanged; send explicit null to clear
    # the cap (the router checks model_fields_set to tell the two apart).
    budget_usd: Optional[float] = Field(default=None, ge=0.0, le=1_000_000)
    training_config: Optional[TrainingConfig] = None


class UploadResponse(BaseModel):
    status: Literal["uploaded"] = "uploaded"
    bucket: str
    key: str
    size: int


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


class RawColumnProfile(BaseModel):
    """Per-column quick-profile stats for a raw uploaded dataset."""

    name: str
    dtype: str
    missing_pct: float = Field(ge=0.0, le=100.0)
    # Approximate distinct-value count (DuckDB approx_unique / HyperLogLog).
    unique_count: int = Field(ge=0)


class RawDatasetPreview(BaseModel):
    """Head rows + quick profile of a raw uploaded file, pre-prep."""

    path: str
    name: str
    format: Literal["csv", "tsv", "parquet"]
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[RawColumnProfile]
    head_columns: list[str]
    head_rows: list[list[Any]]


class ExperimentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    project_id: Optional[str] = Field(default=None, max_length=_UUID_MAX)
    instructions: Optional[str] = Field(default=None, max_length=_INSTRUCTIONS_MAX)
    tags: Optional[list[str]] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
