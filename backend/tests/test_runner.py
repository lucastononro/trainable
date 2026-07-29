"""Unit tests for services/agent/runner.py — run_agent's reachable
orchestration state machine (spawn -> run -> complete/error/cancel) and its
interplay with the task registry in services/agent/tasks.py.

`_drive_provider` (the LLM/provider boundary) is faked throughout — its own
internals are already covered in depth by test_drive_provider.py. What's
under test here is everything *around* that call: the state_change/error/
timeout/abort events run_agent publishes, whether it re-raises or swallows
each exception class, and whether the task registry (register_task /
is_agent_running / abort_agent / cleanup_session) is left in a consistent
state afterwards — using real asyncio Tasks, not mocked ones, for the
registry tests so the cancellation semantics are real.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from db import async_session
from models import Message
from models import Session as SessionModel
from services.agent import runner
from services.agent.tasks import (
    _running_tasks,
    _session_task_locks,
    _silent_aborts,
    abort_agent,
    is_agent_running,
    register_task,
)


async def _create_session(session_id: str) -> None:
    """run_agent's `_publish` helper persists Message rows FK'd to sessions.id
    (SQLite FK enforcement is on — see db.py), so every test needs a real row."""
    async with async_session() as db:
        db.add(SessionModel(id=session_id))
        await db.commit()


async def _messages(session_id: str) -> list[Message]:
    async with async_session() as db:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.id)
        )
        return list(result.scalars().all())


def _state_changes(msgs: list[Message]) -> list[str]:
    return [
        m.metadata_["state"]
        for m in msgs
        if m.metadata_.get("event_type") == "state_change"
    ]


def _event_types(msgs: list[Message]) -> list[str]:
    return [m.metadata_.get("event_type") for m in msgs]


@pytest.fixture(autouse=True)
def _clear_task_registry():
    """The task registry is module-level state that normally only empties via
    cleanup_session in run_agent's `finally`. If a test fails mid-flight (or a
    future test skips cleanup), entries would leak across tests — make the
    isolation explicit rather than relying on distinct session IDs."""
    yield
    _running_tasks.clear()
    _silent_aborts.clear()
    _session_task_locks.clear()


@pytest.fixture(autouse=True)
def _stub_post_run_hooks(monkeypatch):
    """run_agent's success path fans out into workspace scanning + post-stage
    hooks (S3 sync, validation, metadata extraction) that talk to Modal/
    volume/S3 — orthogonal to the state machine under test and covered by
    their own modules. Stub them so every test stays fast and deterministic."""
    monkeypatch.setattr(runner, "publish_artifacts", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "post_stage_hook", AsyncMock(return_value=None))


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_success_runs_running_then_done_and_returns_text(
    monkeypatch,
):
    session_id = "sess-success"
    await _create_session(session_id)

    calls: list[dict] = []

    async def fake_drive(**kwargs):
        calls.append(kwargs)
        return "final report text"

    monkeypatch.setattr(runner, "_drive_provider", fake_drive)

    text = await runner.run_agent(
        session_id=session_id,
        experiment_id="exp-1",
        stage="eda",
        instructions="profile the dataset",
    )

    assert text == "final report text"
    assert len(calls) == 1
    assert calls[0]["agent_type"] == "eda"
    assert calls[0]["stage"] == "eda"
    assert calls[0]["session_id"] == session_id
    assert calls[0]["depth"] == 0
    assert calls[0]["agent_id"] == "root"

    msgs = await _messages(session_id)
    assert _state_changes(msgs) == ["eda_running", "eda_done"]

    runner.publish_artifacts.assert_awaited_once_with(session_id, "exp-1", "eda")
    runner.post_stage_hook.assert_awaited_once_with(session_id, "exp-1", "eda")


# ---------------------------------------------------------------------------
# TimeoutError path — a provider SDK stalling. Must be contained: no raise,
# a dedicated agent_timeout event, and a "timed_out" terminal state. The
# post-run hooks must NOT fire since the run never actually produced output.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_timeout_error_is_contained(monkeypatch):
    session_id = "sess-timeout"
    await _create_session(session_id)

    async def fake_drive(**kwargs):
        raise TimeoutError("provider stalled")

    monkeypatch.setattr(runner, "_drive_provider", fake_drive)

    text = await runner.run_agent(
        session_id=session_id, experiment_id="exp-2", stage="eda", instructions=""
    )

    # collected_text never got a chance to accumulate anything.
    assert text == ""

    msgs = await _messages(session_id)
    assert "agent_timeout" in _event_types(msgs)
    assert _state_changes(msgs) == ["eda_running", "timed_out"]

    runner.publish_artifacts.assert_not_awaited()
    runner.post_stage_hook.assert_not_awaited()


# ---------------------------------------------------------------------------
# Generic exception path — must mark the run failed AND re-raise (unlike
# TimeoutError/CancelledError, which are contained).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_exception_marks_failed_and_reraises(monkeypatch):
    session_id = "sess-error"
    await _create_session(session_id)

    async def fake_drive(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_drive_provider", fake_drive)

    with pytest.raises(RuntimeError, match="boom"):
        await runner.run_agent(
            session_id=session_id, experiment_id="exp-3", stage="eda", instructions=""
        )

    msgs = await _messages(session_id)
    error_msgs = [m for m in msgs if m.metadata_.get("event_type") == "agent_error"]
    assert len(error_msgs) == 1
    assert error_msgs[0].metadata_.get("error") == "boom"
    assert _state_changes(msgs) == ["eda_running", "failed"]

    runner.publish_artifacts.assert_not_awaited()
    runner.post_stage_hook.assert_not_awaited()


# ---------------------------------------------------------------------------
# CancelledError path — contained (never re-raised, unlike the generic
# Exception branch), and gated by the module-level `_silent_aborts` set that
# `abort_agent(session_id, silent=True)` populates for "quiet" follow-up
# swaps (see routers/sessions.py send_message).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_cancelled_publishes_aborted_when_not_silent(monkeypatch):
    session_id = "sess-cancel-loud"
    await _create_session(session_id)

    async def fake_drive(**kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(runner, "_drive_provider", fake_drive)

    text = await runner.run_agent(
        session_id=session_id, experiment_id="exp-4", stage="eda", instructions=""
    )
    assert text == ""

    msgs = await _messages(session_id)
    assert "agent_aborted" in _event_types(msgs)
    assert _state_changes(msgs) == ["eda_running", "cancelled"]


@pytest.mark.asyncio
async def test_run_agent_cancelled_silent_flag_suppresses_events_and_is_consumed(
    monkeypatch,
):
    session_id = "sess-cancel-silent"
    await _create_session(session_id)
    _silent_aborts.add(session_id)

    async def fake_drive(**kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(runner, "_drive_provider", fake_drive)

    await runner.run_agent(
        session_id=session_id, experiment_id="exp-5", stage="eda", instructions=""
    )

    # The flag is a one-shot: consumed (discarded) regardless of outcome.
    assert session_id not in _silent_aborts

    msgs = await _messages(session_id)
    assert "agent_aborted" not in _event_types(msgs)
    # Only the initial "*_running" transition fired — no terminal state_change
    # when the abort is silent.
    assert _state_changes(msgs) == ["eda_running"]


# ---------------------------------------------------------------------------
# Task-registry bookkeeping — spawn -> run -> complete/cancel using REAL
# asyncio Tasks and the real register_task/abort_agent/is_agent_running from
# services.agent.tasks, mirroring how routers/sessions.py drives run_agent.
# A controllable gate stands in for the provider boundary so the task is
# reliably still "running" when we assert on it mid-flight.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_registry_spawn_run_complete_cycle(monkeypatch):
    session_id = "sess-registry-complete"
    await _create_session(session_id)

    gate = asyncio.Event()
    reached_gate = asyncio.Event()

    async def fake_drive(**kwargs):
        reached_gate.set()
        await gate.wait()
        return "done"

    monkeypatch.setattr(runner, "_drive_provider", fake_drive)

    task = asyncio.create_task(
        runner.run_agent(
            session_id=session_id, experiment_id="exp-6", stage="eda", instructions=""
        )
    )
    await register_task(session_id, task)
    # Let the task run its own DB-backed setup (_load_prev_context /
    # _load_project_context) all the way to the faked provider boundary,
    # rather than a bare `sleep(0)` — which would only guarantee ONE
    # scheduler tick and could still leave the task mid-flight on its own
    # DB call when we act on it next.
    await reached_gate.wait()

    assert is_agent_running(session_id) is True

    gate.set()
    result = await task

    assert result == "done"
    assert is_agent_running(session_id) is False
    # cleanup_session (called from run_agent's `finally`, depth==0) popped it.
    assert session_id not in _running_tasks

    msgs = await _messages(session_id)
    assert _state_changes(msgs) == ["eda_running", "eda_done"]


@pytest.mark.asyncio
async def test_task_registry_spawn_run_abort_cycle(monkeypatch):
    session_id = "sess-registry-abort"
    await _create_session(session_id)

    gate = asyncio.Event()  # never set — simulates a stuck provider call
    reached_gate = asyncio.Event()

    async def fake_drive(**kwargs):
        reached_gate.set()
        await gate.wait()
        return "unreachable"

    monkeypatch.setattr(runner, "_drive_provider", fake_drive)

    task = asyncio.create_task(
        runner.run_agent(
            session_id=session_id, experiment_id="exp-7", stage="eda", instructions=""
        )
    )
    await register_task(session_id, task)
    # Wait until the task has cleared its own DB-backed setup and is truly
    # parked on the provider call before cancelling it — cancelling while
    # it's mid-flight on its own (unrelated) DB query is a real hazard for
    # the shared SQLite test connection, not something this test is meant
    # to exercise.
    await reached_gate.wait()

    assert is_agent_running(session_id) is True

    cancelled = await abort_agent(session_id)

    assert cancelled is True
    assert is_agent_running(session_id) is False
    assert session_id not in _running_tasks

    msgs = await _messages(session_id)
    assert "agent_aborted" in _event_types(msgs)
    assert _state_changes(msgs) == ["eda_running", "cancelled"]


@pytest.mark.asyncio
async def test_task_registry_new_message_swaps_stale_task(monkeypatch):
    """register_task must cancel a still-running previous task for the same
    session rather than leaking it — this is the "followup message arrives
    while the agent is still working" case routers/sessions.py relies on."""
    session_id = "sess-registry-swap"
    await _create_session(session_id)

    stale_started = asyncio.Event()
    stale_cancelled = False

    async def stale_coro():
        nonlocal stale_cancelled
        stale_started.set()
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            stale_cancelled = True
            raise

    stale_task = asyncio.create_task(stale_coro())
    await register_task(session_id, stale_task)
    await stale_started.wait()
    assert is_agent_running(session_id) is True

    async def fake_drive(**kwargs):
        return "new run done"

    monkeypatch.setattr(runner, "_drive_provider", fake_drive)

    new_task = asyncio.create_task(
        runner.run_agent(
            session_id=session_id, experiment_id="exp-8", stage="eda", instructions=""
        )
    )
    await register_task(session_id, new_task)

    result = await new_task
    assert result == "new run done"
    # Await the stale task's cancellation explicitly rather than relying on
    # the event loop having already delivered its CancelledError during one of
    # new_task's suspension points — that ordering is not guaranteed.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stale_task, timeout=1.0)
    assert stale_cancelled is True
    assert is_agent_running(session_id) is False
