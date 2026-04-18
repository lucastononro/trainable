"""Task registry and lifecycle management."""

from __future__ import annotations

import asyncio

# Task registry -- maps session_id -> running asyncio.Task
_running_tasks: dict[str, asyncio.Task] = {}

# Sessions whose abort should be silent (no SSE events for cancellation)
_silent_aborts: set[str] = set()

# Per-session lock to serialize task creation / abort / swap. Without this,
# two concurrent requests (e.g. a followup message racing with a stage start)
# can both pass the "is one running?" guard and both write to _running_tasks,
# orphaning the first task.
_session_task_locks: dict[str, asyncio.Lock] = {}


def get_session_task_lock(session_id: str) -> asyncio.Lock:
    lock = _session_task_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_task_locks[session_id] = lock
    return lock


async def register_task(session_id: str, task: asyncio.Task) -> None:
    """Atomically swap in a new task for a session, cancelling any stale one."""
    async with get_session_task_lock(session_id):
        existing = _running_tasks.get(session_id)
        if existing is not None and not existing.done():
            # Shouldn't happen if callers gate via get_running_task, but be defensive.
            existing.cancel()
        _running_tasks[session_id] = task


async def abort_agent(session_id: str, silent: bool = False) -> bool:
    """Cancel a running agent task. Returns True if a task was cancelled.

    If silent=True, the CancelledError handler won't publish agent_aborted / state_change events.
    """
    async with get_session_task_lock(session_id):
        task = _running_tasks.get(session_id)
        if task is None or task.done():
            return False
        if silent:
            _silent_aborts.add(session_id)
        task.cancel()
    # Wait for cancellation OUTSIDE the lock so abort can't deadlock against
    # cleanup_session paths that also want the lock briefly.
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass
    return True


def cleanup_session(session_id: str) -> None:
    """Remove all per-session state when a session ends."""
    from tools.execute_code import _code_counter, _known_files
    from services.clarifications import cancel_session as cancel_clarifications

    _running_tasks.pop(session_id, None)
    _session_task_locks.pop(session_id, None)
    _known_files.pop(session_id, None)
    _code_counter.pop(session_id, None)
    cancelled = cancel_clarifications(session_id)
    if cancelled:
        import logging

        logging.getLogger(__name__).info(
            "Cancelled %d pending clarifications for session %s", cancelled, session_id
        )
