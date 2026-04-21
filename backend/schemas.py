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


class SandboxConfig(BaseModel):
    gpu: Optional[str] = Field(default=None, max_length=_GPU_MAX)
    timeout: Optional[int] = Field(default=None, ge=10, le=7200)


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
    mentions: Optional[list[Mention]] = Field(default=None, max_length=64)


class ClarificationReply(BaseModel):
    answer: str = Field(..., max_length=_CLARIFICATION_MAX)


class ProjectCreate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    sandbox_config: Optional[SandboxConfig] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    sandbox_config: Optional[SandboxConfig] = None


class ExperimentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=_DESC_MAX)
    project_id: Optional[str] = Field(default=None, max_length=_UUID_MAX)
    instructions: Optional[str] = Field(default=None, max_length=_INSTRUCTIONS_MAX)
