"""Task registry and lifecycle management."""

from __future__ import annotations

import asyncio

# Task registry -- maps session_id -> running asyncio.Task
_running_tasks: dict[str, asyncio.Task] = {}

# Sessions whose abort should be silent (no SSE events for cancellation)
_silent_aborts: set[str] = set()


async def abort_agent(session_id: str, silent: bool = False) -> bool:
    """Cancel a running agent task. Returns True if a task was cancelled.

    If silent=True, the CancelledError handler won't publish agent_aborted / state_change events.
    """
    task = _running_tasks.get(session_id)
    if task is None or task.done():
        return False
    if silent:
        _silent_aborts.add(session_id)
    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass
    return True


def cleanup_session(session_id: str) -> None:
    """Remove all per-session state when a session ends."""
    from tools.execute_code import _code_counter, _known_files

    _running_tasks.pop(session_id, None)
    _known_files.pop(session_id, None)
    _code_counter.pop(session_id, None)
