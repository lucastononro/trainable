"""Pydantic request/response schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ExperimentCreate(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""


class MessageCreate(BaseModel):
    content: str
    run_agent: bool = False
    model: Optional[str] = None
    # Per-agent model overrides: {"eda": "claude-haiku-4-5", "trainer": "claude-opus-4-6"}
    agent_models: Optional[dict[str, str]] = Field(default=None)


class StageStart(BaseModel):
    gpu: Optional[str] = None
    instructions: Optional[str] = None
    model: Optional[str] = None


class ClarificationReply(BaseModel):
    answer: str


class ProjectCreate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ExperimentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[str] = None
    instructions: Optional[str] = None
