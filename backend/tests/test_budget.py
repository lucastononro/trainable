"""Budget guardrail tests — services/budget.py + the runner hard-stop.

Covers:
  - BudgetStatus math (exceeded / remaining / uncapped).
  - Session→project resolution for spend accumulation.
  - The API surface: PATCH budget set/clear, usage endpoints exposing budget.
  - The hard-stop itself: a session whose project is over budget (fake
    UsageEvents over the cap) never drives the provider and lands in the
    clean `budget_exceeded` terminal state; a run that crosses the cap
    mid-flight is halted at the next usage event.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from db import async_session
from models import Experiment, Message, Project
from models import Session as SessionModel
from models import UsageEvent
from services.budget import BudgetExceededError, check_budget, get_budget_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_project(
    budget_usd: float | None,
) -> tuple[str, str, str]:
    """Create project + experiment + session rows. Returns (pid, eid, sid)."""
    pid, eid, sid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with async_session() as db:
        db.add(Project(id=pid, name="Budgeted", budget_usd=budget_usd))
        db.add(Experiment(id=eid, project_id=pid, name="exp", session_id=sid))
        db.add(SessionModel(id=sid, experiment_id=eid, project_id=pid))
        await db.commit()
    return pid, eid, sid


async def _seed_spend(pid: str, sid: str, cost_usd: float, n: int = 1) -> None:
    """Insert n fake LLM UsageEvents of cost_usd each."""
    async with async_session() as db:
        for _ in range(n):
            db.add(
                UsageEvent(
                    session_id=sid,
                    project_id=pid,
                    kind="llm",
                    provider="claude",
                    model="claude-opus-4-7",
                    input_tokens=1000,
                    output_tokens=500,
                    cost_usd=cost_usd,
                )
            )
        await db.commit()


# ---------------------------------------------------------------------------
# services/budget.py unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_status_exceeded_when_spend_over_cap():
    pid, _eid, sid = await _seed_project(budget_usd=1.0)
    await _seed_spend(pid, sid, cost_usd=0.6, n=3)  # $1.80 > $1.00

    status = await get_budget_status(project_id=pid)
    assert status is not None
    assert status.budget_usd == 1.0
    assert status.spent_usd == pytest.approx(1.8)
    assert status.exceeded is True
    assert status.remaining_usd == 0.0

    # Resolution via session works the same and raises on check.
    with pytest.raises(BudgetExceededError) as exc:
        await check_budget(sid)
    assert exc.value.status.project_id == pid


@pytest.mark.asyncio
async def test_no_budget_means_uncapped():
    pid, _eid, sid = await _seed_project(budget_usd=None)
    await _seed_spend(pid, sid, cost_usd=999.0)

    status = await check_budget(sid)  # must not raise
    assert status is not None
    assert status.budget_usd is None
    assert status.exceeded is False
    assert status.remaining_usd is None


@pytest.mark.asyncio
async def test_under_budget_does_not_raise():
    pid, _eid, sid = await _seed_project(budget_usd=5.0)
    await _seed_spend(pid, sid, cost_usd=1.0)

    status = await check_budget(sid)
    assert status is not None
    assert status.exceeded is False
    assert status.remaining_usd == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_unknown_session_returns_none():
    assert await get_budget_status(session_id="nope") is None
    assert await check_budget("nope") is None


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_project_budget_set_and_clear(client, default_project_id):
    # Set a budget.
    resp = await client.patch(
        f"/api/projects/{default_project_id}", json={"budget_usd": 12.5}
    )
    assert resp.status_code == 200
    assert resp.json()["budget_usd"] == 12.5

    # PATCHing something else leaves the budget untouched.
    resp = await client.patch(
        f"/api/projects/{default_project_id}", json={"name": "renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["budget_usd"] == 12.5

    # Explicit null clears the cap.
    resp = await client.patch(
        f"/api/projects/{default_project_id}", json={"budget_usd": None}
    )
    assert resp.status_code == 200
    assert resp.json()["budget_usd"] is None

    # Negative budgets are rejected.
    resp = await client.patch(
        f"/api/projects/{default_project_id}", json={"budget_usd": -1}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_usage_endpoints_include_budget(client):
    pid, _eid, sid = await _seed_project(budget_usd=2.0)
    await _seed_spend(pid, sid, cost_usd=3.0)

    resp = await client.get(f"/api/sessions/{sid}/usage")
    assert resp.status_code == 200
    budget = resp.json()["budget"]
    assert budget["project_id"] == pid
    assert budget["budget_usd"] == 2.0
    assert budget["spent_usd"] == pytest.approx(3.0)
    assert budget["exceeded"] is True

    resp = await client.get(f"/api/projects/{pid}/usage")
    assert resp.status_code == 200
    assert resp.json()["budget"]["exceeded"] is True


# ---------------------------------------------------------------------------
# Runner hard-stop
# ---------------------------------------------------------------------------


class _FakeEvent:
    def __init__(self, kind: str, data: dict | None = None):
        self.kind = kind
        self.data = data or {}


class _FakeProvider:
    """Yields one round of events per run() call, recording each call."""

    def __init__(self, events_per_round, supports_mcp: bool = True):
        self.events_per_round = list(events_per_round)
        self.calls: list[dict] = []
        self.capabilities = MagicMock(supports_mcp=supports_mcp)

    async def run(self, **kwargs) -> AsyncIterator[_FakeEvent]:
        self.calls.append(kwargs)
        round_events = self.events_per_round.pop(0) if self.events_per_round else []
        for ev in round_events:
            yield ev


def _patch_runner_volume(monkeypatch, runner):
    """run_agent touches the Modal volume for project context; stub it out."""
    monkeypatch.setattr(runner, "reload_volume_async", AsyncMock())
    monkeypatch.setattr(runner, "listdir_async", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "read_volume_file_async", AsyncMock(return_value=b""))
    # Post-run hooks also touch the volume/S3 — irrelevant to these tests.
    monkeypatch.setattr(runner, "publish_artifacts", AsyncMock())
    monkeypatch.setattr(runner, "post_stage_hook", AsyncMock())


async def _events_of_type(sid: str, event_type: str) -> list[Message]:
    async with async_session() as db:
        rows = (
            (await db.execute(select(Message).where(Message.session_id == sid)))
            .scalars()
            .all()
        )
    return [m for m in rows if (m.metadata_ or {}).get("event_type") == event_type]


@pytest.mark.asyncio
async def test_run_agent_halts_before_start_when_over_budget(monkeypatch):
    """A session whose project already blew its cap never drives the LLM:
    the run terminates immediately in the `budget_exceeded` state."""
    from services.agent import runner

    pid, eid, sid = await _seed_project(budget_usd=0.5)
    await _seed_spend(pid, sid, cost_usd=1.0)  # over cap before the run

    _patch_runner_volume(monkeypatch, runner)
    provider = _FakeProvider([[_FakeEvent("text", {"text": "should never run"})]])
    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    await runner.run_agent(
        session_id=sid,
        experiment_id=eid,
        stage="chat",
        instructions="",
        user_prompt="hello",
    )

    # Provider was never driven.
    assert provider.calls == []

    # Clean terminal state + a clear message were persisted.
    halted = await _events_of_type(sid, "budget_exceeded")
    assert len(halted) == 1
    meta = halted[0].metadata_ or {}
    assert "Budget limit reached" in meta.get("error", "")
    assert meta.get("budget_usd") == 0.5
    assert meta.get("spent_usd") == pytest.approx(1.0)
    states = await _events_of_type(sid, "state_change")
    assert any((m.metadata_ or {}).get("state") == "budget_exceeded" for m in states)
    # It must NOT be reported as a failure.
    assert not any((m.metadata_ or {}).get("state") == "failed" for m in states)
    assert await _events_of_type(sid, "agent_error") == []


@pytest.mark.asyncio
async def test_run_agent_halts_midrun_when_cap_crossed(monkeypatch):
    """Spend crosses the cap DURING the run: the usage event recorded for the
    first LLM call tips the project over, and the runner halts instead of
    driving another round."""
    from services.agent import runner

    pid, eid, sid = await _seed_project(budget_usd=0.5)
    await _seed_spend(pid, sid, cost_usd=0.4)  # under cap at run start

    _patch_runner_volume(monkeypatch, runner)

    # Recording this run's usage pushes the project past the cap.
    async def _record(**kwargs):
        await _seed_spend(pid, sid, cost_usd=0.2)

    monkeypatch.setattr(runner, "record_llm_usage", _record)
    monkeypatch.setattr(runner, "create_mcp_server", lambda *a, **k: {"type": "sdk"})

    provider = _FakeProvider(
        [
            [
                _FakeEvent("text", {"text": "working…"}),
                _FakeEvent(
                    "usage",
                    {"model": "m", "usage": {"input_tokens": 5, "output_tokens": 3}},
                ),
                _FakeEvent("text", {"text": "AFTER-BUDGET-TEXT"}),
            ],
        ],
        supports_mcp=True,
    )
    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    await runner.run_agent(
        session_id=sid,
        experiment_id=eid,
        stage="chat",
        instructions="",
        user_prompt="hello",
    )

    # The provider WAS started this time…
    assert len(provider.calls) == 1
    # …but the event after the budget-tripping usage event was never consumed.
    messages = await _events_of_type(sid, "agent_message")
    assert not any("AFTER-BUDGET-TEXT" in m.content for m in messages)

    halted = await _events_of_type(sid, "budget_exceeded")
    assert len(halted) == 1
    states = await _events_of_type(sid, "state_change")
    assert any((m.metadata_ or {}).get("state") == "budget_exceeded" for m in states)
