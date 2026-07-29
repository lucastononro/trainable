"""HITL approval-gate tests (issue #108)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services import approvals as approvals_svc
from services import clarifications
from services.approvals import (
    APPROVAL_GATE_PROMPT,
    APPROVAL_SKILL_SLUG,
    apply_approval_gate,
    is_enabled,
    set_enabled,
)
from services.skills import get_skill, load_handler


async def _create_experiment(client, sample_csv, project_id):
    with open(sample_csv, "rb") as f:
        resp = await client.post(
            "/api/experiments",
            data={
                "project_id": project_id,
                "name": "Approvals Test",
                "description": "",
                "instructions": "test",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    body = resp.json()
    return body["id"], body["session_id"]


@pytest.fixture(autouse=True)
def _clean_approval_flags():
    """Approval flags are process-global — isolate every test."""
    approvals_svc._enabled_sessions.clear()
    yield
    approvals_svc._enabled_sessions.clear()


class _PublishRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event_type, data, role=None, **kwargs):
        self.events.append({"type": event_type, "data": data, "role": role})


# ---------------------------------------------------------------------------
# Flag + gate injection
# ---------------------------------------------------------------------------


def test_gate_is_identity_when_disabled():
    """Default path is byte-identical: same skills content, same prompt object."""
    skills = ["execute-code", "tasks"]
    prompt = "You are an agent."
    out_skills, out_prompt = apply_approval_gate("session-x", skills, prompt)
    assert out_skills is skills  # not even copied
    assert out_prompt is prompt  # same string object — provably unchanged
    assert APPROVAL_SKILL_SLUG not in out_skills


def test_gate_injects_skill_and_prompt_when_enabled():
    set_enabled("session-y", True)
    skills = ["execute-code", "tasks"]
    prompt = "You are an agent."
    out_skills, out_prompt = apply_approval_gate("session-y", skills, prompt)
    assert out_skills == ["execute-code", "tasks", APPROVAL_SKILL_SLUG]
    assert skills == ["execute-code", "tasks"]  # input not mutated
    assert out_prompt == prompt + "\n\n" + APPROVAL_GATE_PROMPT
    # No double-append if the skill is somehow already present
    again, _ = apply_approval_gate("session-y", out_skills, prompt)
    assert again.count(APPROVAL_SKILL_SLUG) == 1


def test_set_enabled_toggles():
    assert not is_enabled("s1")
    set_enabled("s1", True)
    assert is_enabled("s1")
    set_enabled("s1", False)
    assert not is_enabled("s1")


def test_skill_is_discoverable():
    skill = get_skill(APPROVAL_SKILL_SLUG)
    assert skill.has_handler
    assert skill.schema["required"] == ["title", "decision"]


# ---------------------------------------------------------------------------
# send_message wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_default_leaves_gates_off(
    client, sample_csv, default_project_id
):
    """A message without the approvals field must not enable gates."""
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)
    with patch("routers.sessions.run_agent", new_callable=AsyncMock):
        resp = await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "go", "run_agent": True},
        )
        assert resp.status_code == 200
        from services.agent.tasks import _running_tasks

        await asyncio.wait_for(_running_tasks[session_id], timeout=5)
    assert not is_enabled(session_id)


@pytest.mark.asyncio
async def test_send_message_with_approvals_enables_and_disables(
    client, sample_csv, default_project_id
):
    _, session_id = await _create_experiment(client, sample_csv, default_project_id)
    from services.agent.tasks import _running_tasks

    with patch("routers.sessions.run_agent", new_callable=AsyncMock):
        resp = await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "go", "run_agent": True, "approvals": True},
        )
        assert resp.status_code == 200
        await asyncio.wait_for(_running_tasks[session_id], timeout=5)
        assert is_enabled(session_id)

        # Toggle off on the next message → flag drops back to default.
        resp = await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "go again", "run_agent": True},
        )
        assert resp.status_code == 200
        await asyncio.wait_for(_running_tasks[session_id], timeout=5)
        assert not is_enabled(session_id)


# ---------------------------------------------------------------------------
# Skill handler — block / approve / edit / timeout
# ---------------------------------------------------------------------------


def _make_handler(session_id: str, recorder: _PublishRecorder):
    create_handler = load_handler(APPROVAL_SKILL_SLUG)
    return create_handler(
        session_id=session_id,
        publish_fn=recorder,
        parent_agent_type="orchestrator",
        parent_agent_id="root",
        parent_parent_agent_id=None,
        current_depth=0,
    )


@pytest.mark.asyncio
async def test_handler_blocks_until_approved():
    recorder = _PublishRecorder()
    handler = _make_handler("sess-appr-1", recorder)

    task = asyncio.create_task(
        handler(
            {
                "title": "Target column",
                "decision": "Predict `churned` as a binary target.",
                "kind": "target_column",
                "context": "Only plausible label in the schema.",
            }
        )
    )
    await asyncio.sleep(0.05)
    assert not task.done()  # blocked on the future

    # The request card was published with the decision as content.
    reqs = [e for e in recorder.events if e["type"] == "approval_request"]
    assert len(reqs) == 1
    req = reqs[0]["data"]
    assert req["title"] == "Target column"
    assert req["kind"] == "target_column"
    assert req["content"] == "Predict `churned` as a binary target."
    approval_id = req["approval_id"]

    # Approve through the shared clarifications registry (what the route does).
    assert clarifications.resolve(
        "sess-appr-1",
        approval_id,
        {"decision": "approve", "answer": "", "answered_by": "user", "timeout": False},
    )
    result = await asyncio.wait_for(task, timeout=5)
    assert "APPROVED" in result["content"][0]["text"]
    assert not result.get("is_error")

    resolved = [e for e in recorder.events if e["type"] == "approval_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["data"]["decision"] == "approve"


@pytest.mark.asyncio
async def test_handler_edit_returns_revision():
    recorder = _PublishRecorder()
    handler = _make_handler("sess-appr-2", recorder)
    task = asyncio.create_task(
        handler({"title": "Model shortlist", "decision": "XGBoost only."})
    )
    await asyncio.sleep(0.05)
    approval_id = recorder.events[0]["data"]["approval_id"]

    clarifications.resolve(
        "sess-appr-2",
        approval_id,
        {
            "decision": "edit",
            "answer": "Also include LightGBM and a logistic baseline.",
            "answered_by": "user",
            "timeout": False,
        },
    )
    result = await asyncio.wait_for(task, timeout=5)
    text = result["content"][0]["text"]
    assert "EDITED" in text
    assert "Also include LightGBM" in text


@pytest.mark.asyncio
async def test_handler_timeout_tells_agent_to_flag():
    recorder = _PublishRecorder()
    handler = _make_handler("sess-appr-3", recorder)
    # load_handler execs the module without registering it in sys.modules, so
    # patch the constant through the closure's module globals instead.
    with patch.dict(handler.__globals__, {"_APPROVAL_TIMEOUT_S": 0.05}):
        result = await asyncio.wait_for(
            handler({"title": "Prep plan", "decision": "Drop rows with NaN."}),
            timeout=5,
        )
    text = result["content"][0]["text"]
    assert "NO RESPONSE" in text
    assert "not explicitly approved" in text
    resolved = [e for e in recorder.events if e["type"] == "approval_resolved"]
    assert resolved[0]["data"]["decision"] == "timeout"


@pytest.mark.asyncio
async def test_handler_requires_title_and_decision():
    recorder = _PublishRecorder()
    handler = _make_handler("sess-appr-4", recorder)
    result = await handler({"title": "", "decision": ""})
    assert result["is_error"]
    assert recorder.events == []  # nothing published, nothing pending


# ---------------------------------------------------------------------------
# Resolve endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_endpoint_resolves_pending(client):
    approval_id, future = clarifications.register(
        session_id="sess-appr-5",
        asker_agent_id="root",
        parent_agent_id=None,
        question="[approval:other] t: d",
        timeout_s=30,
    )
    resp = await client.post(
        f"/api/sessions/sess-appr-5/approvals/{approval_id}",
        json={"decision": "approve"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "decision": "approve"}
    payload = await asyncio.wait_for(future, timeout=5)
    assert payload["decision"] == "approve"
    assert payload["answered_by"] == "user"


@pytest.mark.asyncio
async def test_approval_endpoint_404_when_unknown(client):
    resp = await client.post(
        "/api/sessions/sess-x/approvals/deadbeef",
        json={"decision": "approve"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approval_endpoint_edit_requires_edits(client):
    approval_id, future = clarifications.register(
        session_id="sess-appr-6",
        asker_agent_id="root",
        parent_agent_id=None,
        question="q",
        timeout_s=30,
    )
    resp = await client.post(
        f"/api/sessions/sess-appr-6/approvals/{approval_id}",
        json={"decision": "edit", "edits": "   "},
    )
    assert resp.status_code == 400
    assert not future.done()  # still pending — bad request didn't consume it
    # Clean up the pending future so it doesn't leak across tests.
    clarifications.cancel_session("sess-appr-6")
