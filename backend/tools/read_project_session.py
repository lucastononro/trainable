"""read_project_session tool — pull messages from another session in the same project.

Given a session_id that belongs to the same project as the current agent,
returns a slice of its user/assistant conversation so the agent can reuse
context, reference previous conclusions, or avoid repeating prior work.

Safeguards:
- Refuses to read sessions from a different project (prevents cross-project leakage).
- Only surfaces user/assistant text messages by default (drops tool chatter).
- Hard-caps the response at ~12k chars, with offset/tail for pagination.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from db import async_session
from models import Experiment, Message
from models import Session as SessionModel

logger = logging.getLogger(__name__)

_MAX_RESPONSE_CHARS = 12000
_DEFAULT_TAIL = 30


def create_handler(session_id: str, experiment_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        target_session_id = (args.get("session_id") or "").strip()
        if not target_session_id:
            return {
                "content": [{"type": "text", "text": "session_id is required"}],
                "is_error": True,
            }

        include_tool_events = bool(args.get("include_tool_events") or False)
        tail = args.get("tail")
        offset = max(int(args.get("offset") or 0), 0)
        limit = int(args.get("limit") or 0)

        # Default to tailing the last N messages if no slicing was requested.
        if tail is None and offset == 0 and limit == 0:
            tail = _DEFAULT_TAIL

        try:
            async with async_session() as db:
                # Resolve current project_id.
                cur_exp_row = await db.execute(
                    select(Experiment).where(Experiment.id == experiment_id)
                )
                cur_exp = cur_exp_row.scalar_one_or_none()
                if not cur_exp or not cur_exp.project_id:
                    return {
                        "content": [{
                            "type": "text",
                            "text": "Current experiment has no project — cannot cross-reference sessions.",
                        }],
                        "is_error": True,
                    }
                project_id = cur_exp.project_id

                # Fetch target session and verify it belongs to the same project.
                target_session_row = await db.execute(
                    select(SessionModel).where(SessionModel.id == target_session_id)
                )
                target_session = target_session_row.scalar_one_or_none()
                if not target_session:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Session {target_session_id} not found.",
                        }],
                        "is_error": True,
                    }

                target_exp_row = await db.execute(
                    select(Experiment).where(Experiment.id == target_session.experiment_id)
                )
                target_exp = target_exp_row.scalar_one_or_none()
                if not target_exp or target_exp.project_id != project_id:
                    return {
                        "content": [{
                            "type": "text",
                            "text": (
                                f"Session {target_session_id} does not belong to the "
                                f"current project. Cross-project reads are not allowed."
                            ),
                        }],
                        "is_error": True,
                    }

                # Fetch all messages for that session.
                msgs_result = await db.execute(
                    select(Message)
                    .where(Message.session_id == target_session_id)
                    .order_by(Message.id)
                )
                all_messages = list(msgs_result.scalars().all())
        except Exception as e:
            logger.exception("read_project_session failed")
            return {
                "content": [{"type": "text", "text": f"DB error: {e}"}],
                "is_error": True,
            }

        # Filter: by default, only surface conversation (user messages + agent_message events).
        def _keep(msg: Message) -> bool:
            meta = msg.metadata_ or {}
            event_type = meta.get("event_type") or ""
            # Hide internal hidden messages (e.g. file_attached).
            if meta.get("hidden") is True:
                return False
            if msg.role == "user":
                return True
            if msg.role == "assistant" and event_type in ("agent_message", "report_ready"):
                return True
            if include_tool_events and event_type in ("tool_start", "tool_end", "subagent_start", "subagent_end"):
                return True
            return False

        messages = [m for m in all_messages if _keep(m)]
        total = len(messages)

        if tail is not None:
            start = max(0, total - int(tail))
            sliced = messages[start:]
            used_offset = start
            next_offset = None
        else:
            if limit <= 0:
                limit = 20
            sliced = messages[offset : offset + limit]
            used_offset = offset
            next_offset = offset + limit if offset + limit < total else None

        def _render(msg: Message) -> str:
            meta = msg.metadata_ or {}
            event_type = meta.get("event_type") or ""
            role_label = msg.role.upper()
            if event_type == "report_ready":
                stage = meta.get("stage", "stage")
                return f"[{msg.created_at}] REPORT ({stage}):\n{msg.content or ''}"
            agent_type = meta.get("agent_type")
            prefix = f"[{msg.created_at}] {role_label}"
            if agent_type:
                prefix += f" ({agent_type})"
            return f"{prefix}:\n{msg.content or ''}"

        rendered = "\n\n".join(_render(m) for m in sliced)

        response_truncated = False
        if len(rendered) > _MAX_RESPONSE_CHARS:
            rendered = (
                rendered[:_MAX_RESPONSE_CHARS]
                + f"\n\n…[response capped at {_MAX_RESPONSE_CHARS} chars; "
                  f"narrow with offset/limit]"
            )
            response_truncated = True

        envelope = {
            "session_id": target_session_id,
            "chat_name": target_exp.name,
            "project_id": project_id,
            "total_messages": total,
            "returned": len(sliced),
            "offset": used_offset,
            "next_offset": next_offset,
            "response_truncated": response_truncated,
        }
        header = json.dumps(envelope, default=str)
        return {
            "content": [{"type": "text", "text": f"{header}\n\n{rendered}"}]
        }

    return handler
