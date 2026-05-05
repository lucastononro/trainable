"""tasks tool — agents track multi-step work as a live checklist.

Modeled on Claude Code's TaskCreate / TaskUpdate / TaskList. Three operations:
  - add(subject, active_form?, description?) → returns task_id
  - update(task_id, status?, subject?, active_form?, description?)
  - list() → returns all tasks for the current session

Each add/update emits an SSE event (`task_created` / `task_updated`) so the
studio's Plan tab updates live. Tasks are scoped to the current session and
persist in the `tasks` table — page reloads hydrate from REST first, then
SSE picks up new events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from db import async_session
from models import Task

logger = logging.getLogger(__name__)

VALID_STATUSES = {"pending", "in_progress", "completed"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_task_line(task: dict) -> str:
    icon = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}.get(
        task["status"], "[?]"
    )
    label = (
        task.get("active_form")
        if task["status"] == "in_progress" and task.get("active_form")
        else task["subject"]
    )
    return f"  {icon} #{task['id']:<3} {label}"


def _format_task_list(tasks: list[dict]) -> str:
    if not tasks:
        return "(no tasks yet)"
    counts = {"pending": 0, "in_progress": 0, "completed": 0}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    header = (
        f"Tasks ({counts['completed']} done · "
        f"{counts['in_progress']} in progress · {counts['pending']} pending)"
    )
    lines = [header, ""] + [_format_task_line(t) for t in tasks]
    return "\n".join(lines)


def create_handler(session_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        if not isinstance(args, dict):
            return {
                "content": [{"type": "text", "text": "args must be a dict"}],
                "is_error": True,
            }
        operation = (args.get("operation") or "").lower()

        if operation == "add":
            subject = (args.get("subject") or "").strip()
            if not subject:
                return _err("'subject' is required for add")
            active_form = args.get("active_form") or None
            short_description = args.get("short_description") or ""
            description = args.get("description") or ""
            try:
                async with async_session() as db:
                    t = Task(
                        session_id=session_id,
                        subject=subject,
                        active_form=active_form,
                        short_description=short_description,
                        description=description,
                        status="pending",
                    )
                    db.add(t)
                    await db.commit()
                    await db.refresh(t)
                    payload = t.to_dict()
            except Exception as e:
                logger.exception("tasks.add db error")
                return _err(f"DB error: {e}")

            await publish_fn(session_id, "task_created", payload, role="tool")
            return _ok(f"Task #{payload['id']} added: {subject}")

        if operation == "update":
            tid = args.get("task_id")
            if tid is None:
                return _err("'task_id' is required for update")
            try:
                tid = int(tid)
            except (TypeError, ValueError):
                return _err(f"'task_id' must be an integer, got {tid!r}")

            status = args.get("status")
            if status is not None and status not in VALID_STATUSES:
                return _err(
                    f"invalid status {status!r}. Valid: {sorted(VALID_STATUSES)}"
                )

            try:
                async with async_session() as db:
                    t = await db.get(Task, tid)
                    if t is None or t.session_id != session_id:
                        return _err(f"Task #{tid} not found in this session")
                    if status is not None:
                        t.status = status
                    if "subject" in args and args["subject"]:
                        t.subject = args["subject"].strip()
                    if "active_form" in args:
                        t.active_form = args["active_form"] or None
                    if "short_description" in args:
                        t.short_description = args["short_description"] or ""
                    if "description" in args:
                        t.description = args["description"] or ""
                    t.updated_at = _utcnow()
                    await db.commit()
                    await db.refresh(t)
                    payload = t.to_dict()
            except Exception as e:
                logger.exception("tasks.update db error")
                return _err(f"DB error: {e}")

            await publish_fn(session_id, "task_updated", payload, role="tool")
            return _ok(
                f"Task #{payload['id']} → {payload['status']}: {payload['subject']}"
            )

        if operation == "list":
            try:
                async with async_session() as db:
                    stmt = (
                        select(Task)
                        .where(Task.session_id == session_id)
                        .order_by(Task.id)
                    )
                    result = await db.execute(stmt)
                    rows = [t.to_dict() for t in result.scalars().all()]
            except Exception as e:
                logger.exception("tasks.list db error")
                return _err(f"DB error: {e}")
            return _ok(_format_task_list(rows))

        return _err(f"Unknown operation {operation!r}. Valid: add, update, list")

    return handler


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}
