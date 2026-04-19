"""Additional tests for main.py health endpoint and session router edge cases."""

import pytest

# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# _infer_stage helper (chat is always the entry point in multi-agent mode)
# ---------------------------------------------------------------------------


class TestInferStage:
    def test_always_returns_chat(self):
        from routers.sessions import _infer_stage

        for state in ("created", "running", "done", "failed", "cancelled"):
            assert _infer_stage(state) == "chat"


# ---------------------------------------------------------------------------
# Session creation with invalid experiment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_invalid_experiment(client):
    resp = await client.post("/api/experiments/nonexistent/sessions")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Abort session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_session_not_found(client):
    resp = await client.post("/api/sessions/nonexistent/abort")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_abort_session_not_running(client, sample_csv, default_project_id):
    """Abort when no agent is running returns not_running."""

    async def _create_experiment(c, csv):
        with open(csv, "rb") as f:
            resp = await c.post(
                "/api/experiments",
                data={
                    "project_id": default_project_id,
                    "name": "Test",
                    "description": "",
                    "instructions": "",
                },
                files={"files": ("data.csv", f, "text/csv")},
            )
        return resp.json()["id"], resp.json()["session_id"]

    _, session_id = await _create_experiment(client, sample_csv)
    resp = await client.post(f"/api/sessions/{session_id}/abort")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_running"


# ---------------------------------------------------------------------------
# Send message to nonexistent session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_session_not_found(client):
    resp = await client.post(
        "/api/sessions/nonexistent/messages",
        json={"content": "Hello"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Get metrics with filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_metrics_with_stage_filter(client, sample_csv, default_project_id):
    """Metrics endpoint still supports stage and name query params."""

    async def _create(c, csv):
        with open(csv, "rb") as f:
            resp = await c.post(
                "/api/experiments",
                data={
                    "project_id": default_project_id,
                    "name": "Test",
                    "description": "",
                    "instructions": "",
                },
                files={"files": ("data.csv", f, "text/csv")},
            )
        return resp.json()["session_id"]

    session_id = await _create(client, sample_csv)
    resp = await client.get(
        f"/api/sessions/{session_id}/metrics",
        params={"stage": "train", "name": "loss"},
    )
    assert resp.status_code == 200
    assert resp.json() == []
