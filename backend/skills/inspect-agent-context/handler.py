"""inspect_agent_context tool — read another agent's thought stream as a sliceable string."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select

from db import async_session
from models import Message

from services.skills.visible_events import emit_agent_tool_call, new_call_id

logger = logging.getLogger(__name__)

_MAX_RESPONSE_CHARS = 12000


def _format_block(msg: Message) -> dict:
    """Turn a Message row into a dict carrying its block info + timestamp."""
    meta = msg.metadata_ or {}
    return {
        "id": msg.id,
        "created_at": msg.created_at,
        "block_type": meta.get("block_type", "text"),
        "tool_name": meta.get("tool_name"),
        "tool_use_id": meta.get("tool_use_id"),
        "is_error": meta.get("is_error", False),
        "truncated": meta.get("truncated", False),
        "original_bytes": meta.get("original_bytes"),
        "content": msg.content or "",
    }


def _age_label(created_at: str | None, now: datetime) -> str:
    if not created_at:
        return ""
    try:
        ts = datetime.fromisoformat(created_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return ""
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _render_block(block: dict, now: datetime) -> str:
    """Render a single block as a labelled chunk of text with its timestamp."""
    bt = block["block_type"]
    ts = block.get("created_at") or ""
    age = _age_label(ts, now)
    age_part = f" ({age})" if age else ""
    header = f"[{ts}{age_part}] [#{block['id']}] [{bt}"
    if block.get("tool_name"):
        header += f":{block['tool_name']}"
    if block.get("is_error"):
        header += " ERROR"
    if block.get("truncated"):
        header += f" truncated from {block.get('original_bytes')}B"
    header += "]"
    return f"{header}\n{block['content']}"


def _group_into_turns(blocks: list[dict]) -> list[list[dict]]:
    """Group blocks into turns. A turn is a maximal run of assistant blocks
    bounded by tool_result blocks (which come framed as 'user' role)."""
    turns: list[list[dict]] = []
    current: list[dict] = []
    for b in blocks:
        if b["block_type"] == "tool_result":
            current.append(b)
            turns.append(current)
            current = []
        else:
            current.append(b)
    if current:
        turns.append(current)
    return turns


def create_handler(
    session_id: str,
    publish_fn,
    parent_agent_type: str = "",
    parent_agent_id: str = "root",
    parent_parent_agent_id: str | None = None,
    current_depth: int = 0,
    **kwargs,
):
    """Bind an inspector to the current session."""

    async def handler(args: dict):
        target_agent_id = (args.get("agent_id") or "").strip()
        if not target_agent_id:
            return {
                "content": [{"type": "text", "text": "agent_id is required"}],
                "is_error": True,
            }
        call_id = new_call_id()
        started = time.time()

        mode = args.get("mode") or "blocks"
        if mode not in ("blocks", "chars", "turns"):
            return {
                "content": [{"type": "text", "text": f"Unknown mode: {mode}"}],
                "is_error": True,
            }

        offset = max(int(args.get("offset") or 0), 0)
        limit = int(args.get("limit") or 20)
        head = args.get("head")
        tail = args.get("tail")
        filter_block_types = args.get("filter_block_types") or None
        filter_tool_names = args.get("filter_tool_names") or None

        # Default behavior: if no slicing was requested at all, show the tail.
        if head is None and tail is None and offset == 0 and "limit" not in args:
            tail = 20

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
            logger.exception("inspect_agent_context db error")
            return {
                "content": [{"type": "text", "text": f"DB error: {e}"}],
                "is_error": True,
            }

        # Filter to thought-stream rows for the target agent.
        target_agent_type: str | None = None
        blocks: list[dict] = []
        for r in rows:
            meta = r.metadata_ or {}
            if meta.get("event_type") != "agent_thought":
                continue
            if meta.get("agent_id") != target_agent_id:
                continue
            if target_agent_type is None:
                target_agent_type = meta.get("agent_type")
            blocks.append(_format_block(r))

        if not blocks:
            await emit_agent_tool_call(
                publish_fn,
                session_id,
                call_id=call_id,
                tool_name="inspect_agent_context",
                asker_agent_type=parent_agent_type,
                asker_agent_id=parent_agent_id,
                target_agent_type=None,
                depth=current_depth,
                parent_agent_id=parent_parent_agent_id,
                duration_s=time.time() - started,
                is_error=True,
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"No thought blocks found for agent_id={target_agent_id} "
                            f"in this session. Use list_session_agents to see what's available."
                        ),
                    }
                ]
            }

        # Apply block-type / tool-name filters before slicing.
        if filter_block_types:
            wanted = set(filter_block_types)
            blocks = [b for b in blocks if b["block_type"] in wanted]
        if filter_tool_names:
            wanted_names = set(filter_tool_names)
            blocks = [
                b
                for b in blocks
                if b["block_type"] == "text" or (b.get("tool_name") in wanted_names)
            ]

        total_units = len(blocks) if mode != "turns" else len(_group_into_turns(blocks))
        now = datetime.now(timezone.utc)

        # Slice
        if mode == "blocks":
            if head is not None:
                sl = blocks[: int(head)]
                used_offset = 0
                next_offset = int(head) if int(head) < len(blocks) else None
            elif tail is not None:
                start = max(0, len(blocks) - int(tail))
                sl = blocks[start:]
                used_offset = start
                next_offset = None
            else:
                sl = blocks[offset : offset + limit]
                used_offset = offset
                next_offset = offset + limit if offset + limit < len(blocks) else None
            content_str = "\n\n".join(_render_block(b, now) for b in sl)
            returned = len(sl)

        elif mode == "turns":
            turns = _group_into_turns(blocks)
            if head is not None:
                sl = turns[: int(head)]
                used_offset = 0
                next_offset = int(head) if int(head) < len(turns) else None
            elif tail is not None:
                start = max(0, len(turns) - int(tail))
                sl = turns[start:]
                used_offset = start
                next_offset = None
            else:
                sl = turns[offset : offset + limit]
                used_offset = offset
                next_offset = offset + limit if offset + limit < len(turns) else None
            content_str = "\n\n---TURN---\n\n".join(
                "\n\n".join(_render_block(b, now) for b in turn) for turn in sl
            )
            returned = len(sl)

        else:  # chars
            full = "\n\n".join(_render_block(b, now) for b in blocks)
            total_units = len(full)
            if head is not None:
                content_str = full[: int(head)]
                used_offset = 0
                next_offset = int(head) if int(head) < len(full) else None
            elif tail is not None:
                start = max(0, len(full) - int(tail))
                content_str = full[start:]
                used_offset = start
                next_offset = None
            else:
                content_str = full[offset : offset + limit]
                used_offset = offset
                next_offset = offset + limit if offset + limit < len(full) else None
            returned = len(content_str)

        # Final hard cap so a runaway query can't blow the context window.
        response_truncated = False
        if len(content_str) > _MAX_RESPONSE_CHARS:
            content_str = (
                content_str[:_MAX_RESPONSE_CHARS]
                + f"\n\n…[response capped at {_MAX_RESPONSE_CHARS} chars; "
                f"narrow your slice with offset/limit/filter_block_types]"
            )
            response_truncated = True

        envelope = {
            "agent_id": target_agent_id,
            "agent_type": target_agent_type,
            "mode": mode,
            "offset": used_offset,
            "next_offset": next_offset,
            "total": total_units,
            "returned": returned,
            "response_truncated": response_truncated,
            "now": now.isoformat(),
        }
        header = json.dumps(envelope, default=str)

        await emit_agent_tool_call(
            publish_fn,
            session_id,
            call_id=call_id,
            tool_name="inspect_agent_context",
            asker_agent_type=parent_agent_type,
            asker_agent_id=parent_agent_id,
            target_agent_type=target_agent_type,
            depth=current_depth,
            parent_agent_id=parent_parent_agent_id,
            duration_s=time.time() - started,
        )

        return {"content": [{"type": "text", "text": f"{header}\n\n{content_str}"}]}

    return handler
