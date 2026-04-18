"""Session and message endpoint tests."""

import pytest


async def _create_experiment(client, sample_csv):
    """Helper to create an experiment and return (exp_id, session_id)."""
    with open(sample_csv, "rb") as f:
        resp = await client.post(
            "/api/experiments",
            data={"name": "Session Test", "description": "", "instructions": "test"},
            files={"files": ("data.csv", f, "text/csv")},
        )
    body = resp.json()
    return body["id"], body["session_id"]


@pytest.mark.asyncio
async def test_get_session(client, sample_csv):
    exp_id, session_id = await _create_experiment(client, sample_csv)

    resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == session_id
    assert body["experiment_id"] == exp_id
    assert body["state"] == "created"
    assert body["experiment"]["name"] == "Session Test"
    assert body["messages"] == []
    assert body["artifacts"] == []
    assert body["processed_meta"] is None


@pytest.mark.asyncio
async def test_get_session_not_found(client):
    resp = await client.get("/api/sessions/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_additional_session(client, sample_csv):
    exp_id, _ = await _create_experiment(client, sample_csv)

    resp = await client.post(f"/api/experiments/{exp_id}/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["experiment_id"] == exp_id
    assert body["state"] == "created"


@pytest.mark.asyncio
async def test_send_message(client, sample_csv):
    _, session_id = await _create_experiment(client, sample_csv)

    resp = await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "Hello agent"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "user"
    assert body["content"] == "Hello agent"


@pytest.mark.asyncio
async def test_get_messages(client, sample_csv):
    _, session_id = await _create_experiment(client, sample_csv)

    # Send two messages
    await client.post(f"/api/sessions/{session_id}/messages", json={"content": "First"})
    await client.post(
        f"/api/sessions/{session_id}/messages", json={"content": "Second"}
    )

    resp = await client.get(f"/api/sessions/{session_id}/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 2
    assert msgs[0]["content"] == "First"
    assert msgs[1]["content"] == "Second"


@pytest.mark.asyncio
async def test_get_artifacts_empty(client, sample_csv):
    _, session_id = await _create_experiment(client, sample_csv)

    resp = await client.get(f"/api/sessions/{session_id}/artifacts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_metrics_empty(client, sample_csv):
    _, session_id = await _create_experiment(client, sample_csv)

    resp = await client.get(f"/api/sessions/{session_id}/metrics")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_session_shows_in_experiment(client, sample_csv):
    exp_id, session_id = await _create_experiment(client, sample_csv)

    # Create a second session
    resp = await client.post(f"/api/experiments/{exp_id}/sessions")
    second_session_id = resp.json()["id"]

    # Get experiment — should show 2 sessions
    resp = await client.get(f"/api/experiments/{exp_id}")
    body = resp.json()
    assert len(body["sessions"]) == 2
    session_ids = {s["id"] for s in body["sessions"]}
    assert session_id in session_ids
    assert second_session_id in session_ids
