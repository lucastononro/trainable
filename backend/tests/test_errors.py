"""Sentry error-capture tests: request-lifecycle handler + background-task
spawn boundary (see AGENTS.md "Errors" — background agent work raises
outside the request lifecycle, so FastAPI's exception handler never sees
it; the agent runner must report it explicitly)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from errors import capture_exception, generic_exception_handler


def test_capture_exception_noop_without_dsn():
    """No Sentry DSN configured (the default in tests/dev) -> capture_exception
    must not raise, matching sentry_sdk's own no-init no-op behavior."""
    try:
        raise ValueError("boom")
    except ValueError as exc:
        capture_exception(exc)  # must not raise


def test_capture_exception_swallows_sentry_sdk_failure(monkeypatch):
    """A broken Sentry client must never mask the original exception path."""
    import errors

    def _boom(_exc):
        raise RuntimeError("sentry transport down")

    monkeypatch.setattr(errors.sentry_sdk, "capture_exception", _boom)

    try:
        raise ValueError("boom")
    except ValueError as exc:
        capture_exception(exc)  # must not raise despite the broken SDK call


@pytest.mark.asyncio
async def test_generic_exception_handler_reports_to_sentry(monkeypatch):
    """The request-lifecycle handler must forward unhandled exceptions to
    Sentry in addition to logging + returning the JSON 500."""
    import errors

    mock_capture = MagicMock()
    monkeypatch.setattr(errors, "capture_exception", mock_capture)

    request = MagicMock()
    request.method = "GET"
    request.url.path = "/api/whatever"
    exc = ValueError("kaboom")

    response = await generic_exception_handler(request, exc)

    assert response.status_code == 500
    mock_capture.assert_called_once_with(exc)


@pytest.mark.asyncio
async def test_background_agent_error_captured_by_sentry(
    monkeypatch, client, sample_csv, default_project_id
):
    """The background agent-runner spawn boundary in routers/sessions.py
    must call capture_exception when run_agent() raises, even though the
    exception never crosses the request/response cycle (fire-and-forget
    asyncio.Task started from POST /messages)."""
    import routers.sessions as sessions_router

    mock_capture = MagicMock()
    monkeypatch.setattr(sessions_router, "capture_exception", mock_capture)

    boom = RuntimeError("agent loop exploded")

    async def _raising_run_agent(*_args, **_kwargs):
        raise boom

    monkeypatch.setattr(sessions_router, "run_agent", _raising_run_agent)

    with open(sample_csv, "rb") as f:
        resp = await client.post(
            "/api/experiments",
            data={
                "project_id": default_project_id,
                "name": "Sentry BG Test",
                "description": "",
                "instructions": "test",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    session_id = resp.json()["session_id"]

    resp = await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "trigger the agent", "run_agent": True},
    )
    assert resp.status_code == 200

    # The task is fire-and-forget: it's created *after* the response is
    # built, so it may still be scheduled. Wait for it to actually finish.
    from services.agent.tasks import _running_tasks

    task = _running_tasks.get(session_id)
    assert task is not None
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    except Exception:
        pass  # the task itself swallows `boom` internally; that's expected

    mock_capture.assert_called_once_with(boom)
