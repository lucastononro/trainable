"""Resume-context assembly for interrupted sessions.

When a session is aborted, fails, or times out mid-run, the in-flight agent
loop is gone but three durable traces of its progress remain:

1. persisted tool history (``agent_thought`` Message rows — tool_use /
   tool_result blocks, already truncated at write time),
2. the session's task list (the ``tasks`` skill state), and
3. artifacts already written to the session workspace on the volume.

``build_resume_context`` folds those into a single prompt block the relaunched
agent reads to skip completed steps instead of re-driving the whole
conversation.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from db import async_session
from models import Message, Task
from services.volume import listdir_async, reload_volume_async

logger = logging.getLogger(__name__)

# Caps so a long session can't blow up the relaunched agent's context window.
_MAX_TOOL_BLOCKS = 40
_MAX_BLOCK_CHARS = 500
_MAX_FILES = 100

# States a session can be resumed from. `*_running` is included because a
# backend restart can strand the DB in a running state with no live task.
RESUMABLE_TERMINAL_STATES = {"failed", "cancelled", "timed_out", "done"}


def is_resumable_state(state: str | None) -> bool:
    state = state or ""
    return state in RESUMABLE_TERMINAL_STATES or state.endswith("_running") or state.endswith("_done")


def _clip(text: str, limit: int = _MAX_BLOCK_CHARS) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[+{len(text) - limit} chars]"


async def _load_tool_history(session_id: str) -> list[str]:
    """Render the persisted agent_thought tool blocks as one line each."""
    lines: list[str] = []
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.id)
            )
            for msg in result.scalars().all():
                meta = msg.metadata_ or {}
                if meta.get("event_type") != "agent_thought":
                    continue
                block_type = meta.get("block_type")
                if block_type == "tool_use":
                    tool = meta.get("tool_name") or "tool"
                    lines.append(f"- called `{tool}` with {_clip(msg.content)}")
                elif block_type == "tool_result":
                    marker = "ERROR result" if meta.get("is_error") else "result"
                    lines.append(f"  - {marker}: {_clip(msg.content)}")
    except Exception as e:
        logger.warning("Resume: failed to load tool history for %s: %s", session_id, e)
    # Keep the most recent blocks — the tail is what the agent needs to
    # figure out where the previous run stopped.
    return lines[-_MAX_TOOL_BLOCKS:]


async def _load_task_state(session_id: str) -> list[str]:
    lines: list[str] = []
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Task).where(Task.session_id == session_id).order_by(Task.id)
            )
            for t in result.scalars().all():
                lines.append(f"- [{t.status}] {t.subject}")
    except Exception as e:
        logger.warning("Resume: failed to load tasks for %s: %s", session_id, e)
    return lines


async def _load_workspace_files(session_id: str) -> list[str]:
    paths: list[str] = []
    workspace = f"/sessions/{session_id}"
    try:
        await reload_volume_async()
        for entry in await listdir_async(workspace, recursive=True):
            if entry.type.name != "FILE":
                continue
            paths.append(entry.path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(
            "Resume: failed to list workspace files for %s: %s", session_id, e
        )
    paths.sort()
    if len(paths) > _MAX_FILES:
        extra = len(paths) - _MAX_FILES
        paths = paths[:_MAX_FILES] + [f"…({extra} more)"]
    return paths


async def build_resume_context(session_id: str, prior_state: str) -> str:
    """Assemble the resume-context prompt block for a relaunched agent."""
    tool_lines = await _load_tool_history(session_id)
    task_lines = await _load_task_state(session_id)
    file_lines = await _load_workspace_files(session_id)

    sections = [
        "## Resumed session — recovered progress",
        "",
        f"This session's previous run stopped early (last recorded state: "
        f"`{prior_state}`). The evidence of its progress is below. Use it to "
        "skip steps whose outputs already exist instead of redoing them.",
    ]

    sections.append("\n### Task list at interruption")
    if task_lines:
        sections.append("\n".join(task_lines))
    else:
        sections.append("(no tasks were recorded)")

    sections.append(f"\n### Recent tool activity (last {_MAX_TOOL_BLOCKS} blocks, oldest first)")
    if tool_lines:
        sections.append("\n".join(tool_lines))
    else:
        sections.append("(no tool activity was recorded)")

    sections.append("\n### Files already present in the session workspace")
    if file_lines:
        sections.append("\n".join(f"- {p}" for p in file_lines))
    else:
        sections.append("(the workspace is empty)")

    sections.append(
        "\n### Resume rules\n"
        "- Treat files listed above as completed work: verify cheaply (read / "
        "list) instead of regenerating them.\n"
        "- Continue from the first incomplete step; mark tasks completed as "
        "you confirm or finish them.\n"
        "- If a step's artifact exists but looks partial or corrupt, redo "
        "only that step."
    )

    return "\n".join(sections)


def build_resume_prompt(prior_state: str, mode: str) -> str:
    """The synthetic user prompt the relaunched agent is driven with."""
    verb = "Retry the failed work" if mode == "retry" else "Resume the work"
    return (
        f"{verb} in this session. The previous run stopped early (last "
        f"recorded state: `{prior_state}`). Review the 'Resumed session — "
        "recovered progress' section of your system prompt: skip steps whose "
        "artifacts already exist in the workspace, then continue from the "
        "first incomplete step through to completion. Do not re-ask questions "
        "already answered in the prior conversation."
    )
