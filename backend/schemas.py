"""Pydantic request/response schemas."""

from __future__ import annotations

import re
from typing import Literal, Optional

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
    training_config: Optional[TrainingConfig] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    sandbox_config: Optional[SandboxConfig] = None
    training_config: Optional[TrainingConfig] = None


class ExperimentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    project_id: Optional[str] = Field(default=None, max_length=_UUID_MAX)
    instructions: Optional[str] = Field(default=None, max_length=_INSTRUCTIONS_MAX)
    tags: Optional[list[str]] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
