"""Helpers for skill handlers that want a visible UI surface in the chat.

Distinct from `execute-code` (tool_start/tool_end with code+stdout) and from
`delegate-task` (subagent_start/subagent_end which represent a whole sub-agent
run). Use for skills whose effect is reading data (inspect-agent-context,
list-session-agents, read-project-session) or coordinating with other agents
(request-clarification when the parent answers directly).

Surface contract: the SSE payload deliberately carries NO tool input args and
NO result content. The user only needs to KNOW that an agent did something —
the canonical record (with arguments and result) lives in `agent_thought`
rows and is queryable via `inspect-agent-context`. Friendly names are
resolved on the frontend from `agent_type` strings.
"""

from __future__ import annotations

import uuid


def new_call_id() -> str:
    """Stable id used to attribute a card to its persisted message row."""
    return uuid.uuid4().hex[:12]


async def emit_agent_tool_call(
    publish_fn,
    session_id: str,
    *,
    call_id: str,
    tool_name: str,
    asker_agent_type: str,
    asker_agent_id: str | None = None,
    target_agent_type: str | None = None,
    answerer_agent_type: str | None = None,
    depth: int = 0,
    parent_agent_id: str | None = None,
    duration_s: float = 0.0,
    is_error: bool = False,
):
    """Publish + persist a single "an agent used a tool" event for the chat UI."""
    payload = {
        "call_id": call_id,
        "tool_name": tool_name,
        "asker_agent_type": asker_agent_type,
        "target_agent_type": target_agent_type,
        "answerer_agent_type": answerer_agent_type,
        "depth": depth,
        "duration_s": round(float(duration_s), 2),
        "is_error": bool(is_error),
    }
    agent_meta = {
        "agent_id": asker_agent_id or "root",
        "agent_type": asker_agent_type,
        "parent_agent_id": parent_agent_id,
        "depth": depth,
    }
    await publish_fn(
        session_id,
        "agent_tool_call",
        payload,
        role="system",
        agent_meta=agent_meta,
    )


async def emit_clarification_exchange(
    publish_fn,
    session_id: str,
    *,
    call_id: str,
    asker_agent_type: str,
    asker_agent_id: str,
    answerer_agent_type: str,
    answerer_agent_id: str,
    depth: int = 0,
    duration_s: float = 0.0,
):
    """Publish + persist the "parent answered a sub-agent's clarification
    directly" event."""
    await publish_fn(
        session_id,
        "clarification_exchange",
        {
            "call_id": call_id,
            "asker_agent_type": asker_agent_type,
            "answerer_agent_type": answerer_agent_type,
            "depth": depth,
            "duration_s": round(float(duration_s), 2),
        },
        role="system",
        agent_meta={
            "agent_id": asker_agent_id,
            "agent_type": asker_agent_type,
            "parent_agent_id": answerer_agent_id,
            "depth": depth,
        },
    )
