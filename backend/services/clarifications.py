"""Clarification registry — async futures keyed on (session_id, question_id).

Used by the `request_clarification` tool to pause a sub-agent until either
the parent answers (via an in-process call) or the user replies via the HTTP
endpoint that resolves the future.

Also exposes a per-session asyncio.Semaphore used to bound how many nested
SDK subprocesses we spin up at once (parent suspended + child suspended +
impersonator running can stack up fast under recursion).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class _Pending:
    future: asyncio.Future
    asker_agent_id: str
    parent_agent_id: str | None
    question: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timeout_handle: asyncio.TimerHandle | None = None


# (session_id, question_id) -> _Pending
_pending: dict[tuple[str, str], _Pending] = {}

# session_id -> Semaphore. Caps concurrent nested SDK subprocesses per session.
_session_semaphores: dict[str, asyncio.Semaphore] = {}
_DEFAULT_SEMAPHORE_LIMIT = 8


def get_session_semaphore(session_id: str) -> asyncio.Semaphore:
    sem = _session_semaphores.get(session_id)
    if sem is None:
        sem = asyncio.Semaphore(_DEFAULT_SEMAPHORE_LIMIT)
        _session_semaphores[session_id] = sem
    return sem


def register(
    session_id: str,
    asker_agent_id: str,
    parent_agent_id: str | None,
    question: str,
    timeout_s: float = 120.0,
) -> tuple[str, asyncio.Future]:
    """Register a pending clarification. Returns (question_id, future).

    The caller `await`s the future. On timeout, the future resolves with
    `{"timeout": True, ...}` rather than raising — the sub-agent should treat
    that as a normal tool result and continue with whatever fallback its
    prompt encodes.
    """
    question_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    pending = _Pending(
        future=future,
        asker_agent_id=asker_agent_id,
        parent_agent_id=parent_agent_id,
        question=question,
    )
    _pending[(session_id, question_id)] = pending

    def _on_timeout():
        # `resolve` is the same path the user takes; if the future is already
        # done (raced with a real reply) we just no-op.
        resolve(
            session_id,
            question_id,
            {
                "timeout": True,
                "answered_by": "timeout",
                "answer": (
                    "(no reply within timeout — proceed with your best judgment "
                    "and document the assumption you made)"
                ),
            },
            from_timeout=True,
        )

    pending.timeout_handle = loop.call_later(timeout_s, _on_timeout)
    return question_id, future


def resolve(
    session_id: str,
    question_id: str,
    answer: dict | str,
    *,
    from_timeout: bool = False,
) -> bool:
    """Resolve a pending clarification. Returns True if a future was set."""
    key = (session_id, question_id)
    pending = _pending.get(key)
    if pending is None:
        return False
    if pending.future.done():
        return False
    if isinstance(answer, str):
        payload = {"answer": answer, "answered_by": "user", "timeout": False}
    else:
        payload = answer
    try:
        pending.future.set_result(payload)
    except asyncio.InvalidStateError:
        return False
    if pending.timeout_handle and not from_timeout:
        pending.timeout_handle.cancel()
    # Don't pop on timeout immediately so a late user reply can race-detect.
    _pending.pop(key, None)
    return True


def cancel_session(session_id: str) -> int:
    """Cancel every pending clarification for a session. Used by cleanup_session."""
    n = 0
    for key in list(_pending.keys()):
        if key[0] != session_id:
            continue
        pending = _pending.pop(key, None)
        if pending is None:
            continue
        if pending.timeout_handle:
            pending.timeout_handle.cancel()
        if not pending.future.done():
            try:
                pending.future.set_result(
                    {
                        "answer": "(session ended before a reply was received)",
                        "answered_by": "session_ended",
                        "timeout": False,
                    }
                )
            except asyncio.InvalidStateError:
                pass
            n += 1
    _session_semaphores.pop(session_id, None)
    return n


def list_pending(session_id: str) -> list[dict]:
    """Inspector helper — returns metadata for any outstanding clarifications in a session."""
    out = []
    for (sid, qid), pending in _pending.items():
        if sid != session_id:
            continue
        out.append(
            {
                "question_id": qid,
                "asker_agent_id": pending.asker_agent_id,
                "parent_agent_id": pending.parent_agent_id,
                "question": pending.question,
                "created_at": pending.created_at.isoformat(),
                "done": pending.future.done(),
            }
        )
    return out
