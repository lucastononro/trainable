"""Pydantic request/response schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ExperimentCreate(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""


class MessageCreate(BaseModel):
    content: str
    run_agent: bool = False
    model: Optional[str] = None


class StageStart(BaseModel):
    gpu: Optional[str] = None
    instructions: Optional[str] = None
    model: Optional[str] = None
