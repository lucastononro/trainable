"""Resume / retry endpoint tests (issue #106)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from db import async_session
from models import Message, Task
from models import Session as SessionModel
from services.agent.resume import build_resume_context, is_resumable_state
from tests.conftest import MockVolume


async def _create_experiment(client, sample_csv, project_id):
    with open(sample_csv, "rb") as f:
        resp = await client.post(
            "/api/experiments",
            data={
                "project_id": project_id,
                "name": "Resume Test",
                "description": "",
                "instructions": "test",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    body = resp.json()
    return body["id"], body["session_id"]


async def _set_session_state(session_id: str, state: str):
    async with async_session() as db:
        s = await db.get(SessionModel, session_id)
        s.state = state
        await db.commit()


def _patch_resume_volume(files: dict[str, bytes]):
    vol = MockVolume(files)
    return [
        patch("services.agent.resume.reload_volume_async", new_callable=AsyncMock),
        patch(
            "services.agent.resume.listdir_async",
            new_callable=AsyncMock,
            side_effect=vol.listdir,
        ),
    ]


# ---------------------------------------------------------------------------
# Endpoint guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_session_not_found(client):
    resp = await client.post("/api/sessions/nonexistent/resume")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resume_created_session_is_rejected(client, sample_csv, default_project_id):
    """A fresh session has no prior run — nothing to resume."""
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)
    resp = await client.post(f"/api/sessions/{session_id}/resume")
    assert resp.status_code == 400
    assert "no prior run" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_resume_while_running_conflicts(client, sample_csv, default_project_id):
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)
    await _set_session_state(session_id, "failed")
    with patch("routers.sessions.is_agent_running", return_value=True):
        resp = await client.post(f"/api/sessions/{session_id}/resume")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Relaunch path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_failed_session_relaunches_agent(
    client, sample_csv, default_project_id
):
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)
    await _set_session_state(session_id, "failed")

    with (
        patch("routers.sessions.run_agent", new_callable=AsyncMock) as mock_run,
        patch(
            "routers.sessions.build_resume_context",
            new_callable=AsyncMock,
            return_value="## Resumed session — recovered progress\n(canned)",
        ) as mock_ctx,
    ):
        resp = await client.post(f"/api/sessions/{session_id}/resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"status": "resumed", "mode": "resume", "prior_state": "failed"}

        # The launch is async — wait for the registered task to finish.
        from services.agent.tasks import _running_tasks

        task = _running_tasks.get(session_id)
        assert task is not None
        await asyncio.wait_for(task, timeout=5)

        mock_ctx.assert_awaited_once_with(session_id, "failed")
        mock_run.assert_awaited_once()
        kwargs = mock_run.await_args.kwargs
        assert kwargs["session_id"] == session_id
        assert kwargs["stage"] == "chat"
        assert kwargs["resume_context"].startswith("## Resumed session")
        assert "Resume the work" in kwargs["user_prompt"]
        assert "failed" in kwargs["user_prompt"]

    # Mocked run_agent returned cleanly → the wrapper marks the session done.
    resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.json()["state"] == "done"


@pytest.mark.asyncio
async def test_retry_mode_shades_the_prompt(client, sample_csv, default_project_id):
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)
    await _set_session_state(session_id, "failed")

    with (
        patch("routers.sessions.run_agent", new_callable=AsyncMock) as mock_run,
        patch(
            "routers.sessions.build_resume_context",
            new_callable=AsyncMock,
            return_value="ctx",
        ),
    ):
        resp = await client.post(
            f"/api/sessions/{session_id}/resume", json={"mode": "retry"}
        )
        assert resp.status_code == 200
        assert resp.json()["mode"] == "retry"

        from services.agent.tasks import _running_tasks

        await asyncio.wait_for(_running_tasks[session_id], timeout=5)
        assert "Retry the failed work" in mock_run.await_args.kwargs["user_prompt"]


@pytest.mark.asyncio
async def test_resume_publishes_sse_event(client, sample_csv, default_project_id):
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)
    await _set_session_state(session_id, "cancelled")

    with (
        patch("routers.sessions.run_agent", new_callable=AsyncMock),
        patch(
            "routers.sessions.build_resume_context",
            new_callable=AsyncMock,
            return_value="ctx",
        ),
        patch("routers.sessions.broadcaster.publish", new_callable=AsyncMock) as mock_pub,
    ):
        resp = await client.post(f"/api/sessions/{session_id}/resume")
        assert resp.status_code == 200
        from services.agent.tasks import _running_tasks

        await asyncio.wait_for(_running_tasks[session_id], timeout=5)

    events = [c.args[1] for c in mock_pub.await_args_list]
    resumed = [e for e in events if e["type"] == "session_resumed"]
    assert len(resumed) == 1
    assert resumed[0]["data"] == {"mode": "resume", "prior_state": "cancelled"}


# ---------------------------------------------------------------------------
# Resume-context assembly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_resume_context_sections(client, sample_csv, default_project_id):
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)

    async with async_session() as db:
        db.add_all(
            [
                Message(
                    session_id=session_id,
                    role="user",
                    content="train a model on iris",
                ),
                Message(
                    session_id=session_id,
                    role="assistant",
                    content='{"code": "df.describe()"}',
                    metadata_={
                        "event_type": "agent_thought",
                        "block_type": "tool_use",
                        "tool_name": "execute-code",
                    },
                ),
                Message(
                    session_id=session_id,
                    role="user",
                    content="Traceback: boom",
                    metadata_={
                        "event_type": "agent_thought",
                        "block_type": "tool_result",
                        "is_error": True,
                    },
                ),
            ]
        )
        db.add(Task(session_id=session_id, subject="Run EDA", status="completed"))
        db.add(Task(session_id=session_id, subject="Train model", status="pending"))
        await db.commit()

    files = {
        f"/sessions/{session_id}/report.md": b"# EDA",
        f"/sessions/{session_id}/data/train.parquet": b"pq",
    }
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _patch_resume_volume(files):
            stack.enter_context(p)
        ctx = await build_resume_context(session_id, "failed")

    assert "last recorded state: `failed`" in ctx
    # Tool history (only agent_thought rows, with the error marker)
    assert "called `execute-code`" in ctx
    assert "ERROR result: Traceback: boom" in ctx
    # Plain chat messages are NOT duplicated into the tool-history block
    assert "train a model on iris" not in ctx
    # Task state
    assert "- [completed] Run EDA" in ctx
    assert "- [pending] Train model" in ctx
    # Workspace listing
    assert f"- /sessions/{session_id}/report.md" in ctx
    assert f"- /sessions/{session_id}/data/train.parquet" in ctx


@pytest.mark.asyncio
async def test_build_resume_context_empty_session(
    client, sample_csv, default_project_id
):
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _patch_resume_volume({}):
            stack.enter_context(p)
        ctx = await build_resume_context(session_id, "cancelled")

    assert "(no tasks were recorded)" in ctx
    assert "(no tool activity was recorded)" in ctx
    assert "(the workspace is empty)" in ctx


def test_is_resumable_state():
    assert is_resumable_state("failed")
    assert is_resumable_state("cancelled")
    assert is_resumable_state("timed_out")
    assert is_resumable_state("done")
    assert is_resumable_state("chat_running")  # stale after backend restart
    assert is_resumable_state("eda_done")
    assert not is_resumable_state("created")
    assert not is_resumable_state(None)
    assert not is_resumable_state("")


# ---------------------------------------------------------------------------
# Legacy behavior stays byte-identical when resume isn't used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_path_untouched_by_resume_feature(
    client, sample_csv, default_project_id
):
    """The normal follow-up launch must not carry any resume kwargs."""
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)

    with patch("routers.sessions.run_agent", new_callable=AsyncMock) as mock_run:
        resp = await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello", "run_agent": True},
        )
        assert resp.status_code == 200
        from services.agent.tasks import _running_tasks

        await asyncio.wait_for(_running_tasks[session_id], timeout=5)

    mock_run.assert_awaited_once()
    kwargs = mock_run.await_args.kwargs
    assert "resume_context" not in kwargs
    assert kwargs["user_prompt"] == "hello"
