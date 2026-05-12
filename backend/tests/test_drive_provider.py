"""Unit tests for runner._drive_provider — the core multi-provider loop.

Two paths to cover:
  - Claude/MCP path: provider.run is the only loop driver; runner just
    forwards events and records usage.
  - Non-Claude path: runner manages messages list, dispatches tool_call
    events to the matching skill handler, feeds results back, repeats.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest


class _FakeEvent:
    def __init__(self, kind: str, data: dict | None = None):
        self.kind = kind
        self.data = data or {}


class _FakeProvider:
    def __init__(
        self, events_per_round: list[list[_FakeEvent]], supports_mcp: bool = False
    ):
        self.events_per_round = list(events_per_round)
        self.calls: list[dict] = []
        self.capabilities = MagicMock(supports_mcp=supports_mcp)

    async def run(self, **kwargs) -> AsyncIterator[_FakeEvent]:
        self.calls.append(kwargs)
        round_events = self.events_per_round.pop(0) if self.events_per_round else []
        for ev in round_events:
            yield ev


@pytest.fixture
def patched_runner(monkeypatch):
    """Stub runner deps that hit the DB / observability."""
    from services.agent import runner

    async def _record(*a, **k):
        pass

    monkeypatch.setattr(runner, "record_llm_usage", _record)

    # The non-Claude path doesn't need create_mcp_server, but the Claude path does.
    monkeypatch.setattr(
        runner,
        "create_mcp_server",
        lambda *a, **k: {"type": "sdk", "instance": object()},
    )

    # Make build_skill_entries return whatever the test injects via the registry mock.
    monkeypatch.setattr(
        runner, "build_skill_entries", lambda **kw: kw.get("_entries", {})
    )
    yield runner


def _make_publish():
    """Capture what the runner publishes so assertions can inspect it."""
    events: list[tuple] = []

    async def publish(
        event_type: str, data: dict, role: str | None = None, publish: bool = True
    ):
        events.append((event_type, data, role, publish))

    return publish, events


def _stub_agent(monkeypatch, skills: list[str]):
    import services.agent.agents as agents

    monkeypatch.setattr(agents, "get_agent_skills", lambda _t: skills)
    monkeypatch.setattr(
        agents,
        "get_skill_for_agent",
        lambda agent_type, slug: {
            "name": slug,
            "description": f"{slug} desc",
            "input_schema": {"type": "object", "properties": {}},
        },
    )


@pytest.mark.asyncio
async def test_non_claude_loop_dispatches_skill_handler(patched_runner, monkeypatch):
    """When the provider yields tool_call, the runner must call the handler
    and feed the result back into the next round."""
    runner = patched_runner

    handler_calls: list[dict] = []

    async def fake_handler(args):
        handler_calls.append(args)
        return {"content": [{"type": "text", "text": "tool ran ok"}]}

    # Inject the entries into build_skill_entries via a sentinel key.
    monkeypatch.setattr(
        runner,
        "build_skill_entries",
        lambda **kw: {
            "echo": {
                "description": "echo desc",
                "input_schema": {},
                "handler": fake_handler,
            }
        },
    )

    _stub_agent(monkeypatch, skills=["echo"])

    # Round 1: tool_call. Round 2: text + (no tool_call) -> loop ends.
    provider = _FakeProvider(
        [
            [
                _FakeEvent(
                    "tool_call",
                    {"tool_name": "echo", "tool_call_id": "c1", "arguments": {"x": 1}},
                ),
                _FakeEvent(
                    "usage",
                    {"model": "m", "usage": {"input_tokens": 5, "output_tokens": 3}},
                ),
            ],
            [_FakeEvent("text", {"text": "all done"})],
        ],
        supports_mcp=False,
    )

    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    publish, events = _make_publish()

    text = await runner._drive_provider(
        provider_id="openai",
        prompt="user task",
        system_prompt="sys",
        model="gpt-x",
        agent_type="eda",
        session_id="s",
        experiment_id="e",
        stage="eda",
        depth=0,
        agent_id="root",
        parent_agent_id=None,
        agent_skills=["echo"],
        sandbox_config={},
        instructions="",
        agent_models={},
        publish=publish,
        agent_span=MagicMock(),
    )

    assert "all done" in text
    assert len(handler_calls) == 1
    assert handler_calls[0] == {"x": 1}

    # Provider was called twice (round 1 + round 2 after tool dispatch).
    assert len(provider.calls) == 2

    # Second call's messages should contain the tool result we fed back in.
    second_messages = provider.calls[1]["messages"]
    assert any(
        m["role"] == "tool" and "tool ran ok" in m["content"] for m in second_messages
    )


@pytest.mark.asyncio
async def test_non_claude_loop_terminates_when_no_tool_calls(
    patched_runner, monkeypatch
):
    """Provider returns text only -> single round, no handler dispatch."""
    runner = patched_runner

    monkeypatch.setattr(runner, "build_skill_entries", lambda **kw: {})
    _stub_agent(monkeypatch, skills=[])

    provider = _FakeProvider(
        [
            [_FakeEvent("text", {"text": "hello world"})],
        ],
        supports_mcp=False,
    )
    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    publish, events = _make_publish()

    text = await runner._drive_provider(
        provider_id="gemini",
        prompt="hi",
        system_prompt="sys",
        model="m",
        agent_type="eda",
        session_id="s",
        experiment_id="e",
        stage="eda",
        depth=0,
        agent_id="root",
        parent_agent_id=None,
        agent_skills=[],
        sandbox_config={},
        instructions="",
        agent_models={},
        publish=publish,
        agent_span=MagicMock(),
    )

    assert text == "hello world"
    assert len(provider.calls) == 1
    # Frontend-facing event was emitted.
    assert any(
        et == "agent_message" and d.get("text") == "hello world"
        for et, d, _, _ in events
    )


@pytest.mark.asyncio
async def test_non_claude_unknown_skill_marks_error(patched_runner, monkeypatch):
    """When the provider names a skill we don't have, runner emits an
    is_error tool_result and continues."""
    runner = patched_runner

    monkeypatch.setattr(runner, "build_skill_entries", lambda **kw: {})
    _stub_agent(monkeypatch, skills=[])

    provider = _FakeProvider(
        [
            [
                _FakeEvent(
                    "tool_call",
                    {"tool_name": "ghost", "tool_call_id": "c1", "arguments": {}},
                )
            ],
            [_FakeEvent("text", {"text": "ok"})],
        ],
        supports_mcp=False,
    )
    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    publish, events = _make_publish()

    await runner._drive_provider(
        provider_id="openai",
        prompt="x",
        system_prompt="sys",
        model="m",
        agent_type="eda",
        session_id="s",
        experiment_id="e",
        stage="eda",
        depth=0,
        agent_id="root",
        parent_agent_id=None,
        agent_skills=[],
        sandbox_config={},
        instructions="",
        agent_models={},
        publish=publish,
        agent_span=MagicMock(),
    )

    # Tool result event should carry is_error=True.
    tool_results = [d for et, d, _, _ in events if d.get("block_type") == "tool_result"]
    assert tool_results, "expected at least one tool_result thought event"
    assert any(t.get("is_error") for t in tool_results)


@pytest.mark.asyncio
async def test_claude_path_passes_mcp_server(patched_runner, monkeypatch):
    """For supports_mcp providers, the runner builds the MCP server itself
    and passes it through provider.run()."""
    runner = patched_runner

    _stub_agent(monkeypatch, skills=["execute-code"])

    provider = _FakeProvider(
        [
            [
                _FakeEvent("text", {"text": "claude reply"}),
                _FakeEvent(
                    "usage",
                    {
                        "model": "claude-x",
                        "usage": {"input_tokens": 10, "output_tokens": 4},
                    },
                ),
            ],
        ],
        supports_mcp=True,
    )
    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    publish, events = _make_publish()

    text = await runner._drive_provider(
        provider_id="claude",
        prompt="hi",
        system_prompt="sys",
        model="claude-x",
        agent_type="eda",
        session_id="s",
        experiment_id="e",
        stage="eda",
        depth=0,
        agent_id="root",
        parent_agent_id=None,
        agent_skills=["execute-code"],
        sandbox_config={},
        instructions="",
        agent_models={},
        publish=publish,
        agent_span=MagicMock(),
    )

    assert text == "claude reply"
    assert len(provider.calls) == 1
    # Claude path passes mcp_servers, NOT messages.
    call = provider.calls[0]
    assert "mcp_servers" in call
    assert call["mcp_servers"]["trainable"] is not None
    assert call.get("messages") is None
    # Tool names were prefixed with the MCP namespace.
    assert any(t.startswith("mcp__trainable__") for t in call["tools"])


# ---------------------------------------------------------------------------
# Regression: thinking_level must be plumbed into provider.run().
# Was computed in run_agent but never forwarded — UI picker had no effect.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_level_forwards_reasoning_effort_to_openai(
    patched_runner, monkeypatch
):
    runner = patched_runner

    monkeypatch.setattr(runner, "build_skill_entries", lambda **kw: {})
    _stub_agent(monkeypatch, skills=[])

    provider = _FakeProvider(
        [[_FakeEvent("text", {"text": "ok"})]],
        supports_mcp=False,
    )
    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    publish, _ = _make_publish()

    await runner._drive_provider(
        provider_id="openai",
        prompt="hi",
        system_prompt="sys",
        model="gpt-5.5-mini",
        agent_type="eda",
        session_id="s",
        experiment_id="e",
        stage="eda",
        depth=0,
        agent_id="root",
        parent_agent_id=None,
        agent_skills=[],
        sandbox_config={},
        instructions="",
        agent_models={},
        publish=publish,
        agent_span=MagicMock(),
        thinking_level="high",
    )

    assert provider.calls, "provider.run was never called"
    assert provider.calls[0].get("reasoning_effort") == "high"


@pytest.mark.asyncio
async def test_thinking_level_off_does_not_spread_kwargs(patched_runner, monkeypatch):
    runner = patched_runner

    monkeypatch.setattr(runner, "build_skill_entries", lambda **kw: {})
    _stub_agent(monkeypatch, skills=[])

    provider = _FakeProvider(
        [[_FakeEvent("text", {"text": "ok"})]],
        supports_mcp=False,
    )
    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    publish, _ = _make_publish()

    await runner._drive_provider(
        provider_id="openai",
        prompt="hi",
        system_prompt="sys",
        # GPT-5 / o-series — "off" maps to "minimal", confirming the
        # thinking config path actually fired.
        model="gpt-5",
        agent_type="eda",
        session_id="s",
        experiment_id="e",
        stage="eda",
        depth=0,
        agent_id="root",
        parent_agent_id=None,
        agent_skills=[],
        sandbox_config={},
        instructions="",
        agent_models={},
        publish=publish,
        agent_span=MagicMock(),
        thinking_level="off",
    )

    assert provider.calls[0].get("reasoning_effort") == "minimal"


@pytest.mark.asyncio
async def test_no_thinking_level_means_no_kwarg(patched_runner, monkeypatch):
    runner = patched_runner

    monkeypatch.setattr(runner, "build_skill_entries", lambda **kw: {})
    _stub_agent(monkeypatch, skills=[])

    provider = _FakeProvider(
        [[_FakeEvent("text", {"text": "ok"})]],
        supports_mcp=False,
    )
    monkeypatch.setattr(runner.llm_factory, "get_provider", lambda _id: provider)

    publish, _ = _make_publish()

    await runner._drive_provider(
        provider_id="openai",
        prompt="hi",
        system_prompt="sys",
        model="gpt-5",
        agent_type="eda",
        session_id="s",
        experiment_id="e",
        stage="eda",
        depth=0,
        agent_id="root",
        parent_agent_id=None,
        agent_skills=[],
        sandbox_config={},
        instructions="",
        agent_models={},
        publish=publish,
        agent_span=MagicMock(),
        # thinking_level omitted → no provider kwargs
    )

    assert "reasoning_effort" not in provider.calls[0]
