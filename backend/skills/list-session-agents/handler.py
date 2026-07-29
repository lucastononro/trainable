"""list_session_agents tool — discover all agents that have run in this session."""

from __future__ import annotations

import json
import logging
import time

from sqlalchemy import select

from db import async_session
from models import Message

from services.skills.visible_events import emit_agent_tool_call, new_call_id

logger = logging.getLogger(__name__)


def create_handler(
    session_id: str,
    publish_fn,
    parent_agent_type: str = "",
    parent_agent_id: str = "root",
    parent_parent_agent_id: str | None = None,
    current_depth: int = 0,
    **kwargs,
):
    async def handler(args: dict):
        call_id = new_call_id()
        started = time.time()
        try:
            async with async_session() as db:
                stmt = (
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.id)
                )
                result = await db.execute(stmt)
                rows = list(result.scalars().all())
        except Exception as e:
            logger.exception("list_session_agents db error")
            return {
                "content": [{"type": "text", "text": f"DB error: {e}"}],
                "is_error": True,
            }

        # Aggregate per agent_id from message metadata.
        agents: dict[str, dict] = {}
        for r in rows:
            meta = r.metadata_ or {}
            aid = meta.get("agent_id")
            if not aid:
                continue
            entry = agents.get(aid)
            if entry is None:
                entry = {
                    "agent_id": aid,
                    "agent_type": meta.get("agent_type"),
                    "parent_agent_id": meta.get("parent_agent_id"),
                    "depth": meta.get("depth", 0),
                    "block_count": 0,
                    "thought_count": 0,
                    "started_at": r.created_at,
                    "ended_at": r.created_at,
                }
                agents[aid] = entry
            entry["block_count"] += 1
            if meta.get("event_type") == "agent_thought":
                entry["thought_count"] += 1
            entry["ended_at"] = r.created_at
            # Backfill agent_type if a later row has it and the first one didn't.
            if entry["agent_type"] is None and meta.get("agent_type"):
                entry["agent_type"] = meta.get("agent_type")
            if entry["parent_agent_id"] is None and meta.get("parent_agent_id"):
                entry["parent_agent_id"] = meta.get("parent_agent_id")

        ordered = sorted(
            agents.values(), key=lambda a: (a.get("depth") or 0, a["started_at"] or "")
        )

        await emit_agent_tool_call(
            publish_fn,
            session_id,
            call_id=call_id,
            tool_name="list_session_agents",
            asker_agent_type=parent_agent_type,
            asker_agent_id=parent_agent_id,
            depth=current_depth,
            parent_agent_id=parent_parent_agent_id,
            duration_s=time.time() - started,
        )

        if not ordered:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "(no agents have produced messages in this session yet)",
                    }
                ]
            }

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"agents": ordered}, default=str, indent=2),
                }
            ]
        }

    return handler
