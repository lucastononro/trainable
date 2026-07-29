"""Claude OAuth usage-fallback regression test.

Reproduces the case where claude-agent-sdk's `ResultMessage.usage` /
`.model_usage` are empty (the typical path for Claude Code OAuth /
subscription users) and verifies the provider falls back to per-turn
AssistantMessage.usage so the run still gets attributed token counts.
"""

from __future__ import annotations

import pytest

from services.llm.base import LLMEvent
from services.llm.claude_provider import ClaudeProvider


class _FakeBlock:
    def __init__(self, text: str | None = None):
        self.text = text


class _FakeAssistantMessage:
    """Mimics claude_agent_sdk.AssistantMessage shape."""

    def __init__(self, *, model: str, usage: dict, text: str = ""):
        self.model = model
        self.usage = usage
        self.content = [_FakeBlock(text=text)] if text else []


class _FakeResultMessage:
    """Mimics claude_agent_sdk.ResultMessage shape."""

    def __init__(
        self,
        *,
        model_usage: dict | None = None,
        usage: dict | None = None,
        total_cost_usd: float | None = None,
    ):
        self.model_usage = model_usage
        self.usage = usage
        self.total_cost_usd = total_cost_usd


def _patch_query(monkeypatch, messages: list):
    """Replace claude_agent_sdk.query() and the imported binding inside
    claude_provider with an async generator yielding the supplied messages."""

    async def _fake_query(*args, **kwargs):
        for m in messages:
            yield m

    # The provider does `from claude_agent_sdk import query` and `from
    # claude_agent_sdk import AssistantMessage / ResultMessage`. We need to
    # patch BOTH the binding inside the provider module (used for the call)
    # AND the isinstance checks (which compare against the SDK types).
    import services.llm.claude_provider as cp_module

    monkeypatch.setattr(cp_module, "query", _fake_query)
    monkeypatch.setattr(cp_module, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(cp_module, "ResultMessage", _FakeResultMessage)
    # UserMessage isn't exercised here; leave the real class in place so
    # isinstance checks against unrelated objects don't accidentally match.

    # Also stub ClaudeAgentOptions so we don't need its real signature.
    monkeypatch.setattr(cp_module, "ClaudeAgentOptions", lambda **kw: kw)


@pytest.mark.asyncio
async def test_oauth_path_falls_back_to_assistant_usage(monkeypatch):
    """ResultMessage usage is empty (OAuth case); per-turn AssistantMessage
    usage has real token counts. The provider must yield a final usage
    event derived from the accumulator."""
    messages = [
        _FakeAssistantMessage(
            model="claude-sonnet-4-6",
            usage={
                "input_tokens": 120,
                "output_tokens": 40,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            text="thinking…",
        ),
        _FakeAssistantMessage(
            model="claude-sonnet-4-6",
            usage={
                "input_tokens": 80,
                "output_tokens": 60,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 0,
            },
            text="more…",
        ),
        _FakeResultMessage(
            model_usage=None,
            usage=None,
            total_cost_usd=None,
        ),
    ]
    _patch_query(monkeypatch, messages)

    provider = ClaudeProvider()
    events: list[LLMEvent] = []
    async for ev in provider.run(
        prompt="hi", system_prompt="", model="claude-sonnet-4-6"
    ):
        events.append(ev)

    usage_events = [ev for ev in events if ev.kind == "usage"]
    partials = [ev for ev in usage_events if ev.data.get("partial")]
    finals = [ev for ev in usage_events if not ev.data.get("partial")]

    # 2 partials (one per AssistantMessage), 1 final from the accumulator.
    assert len(partials) == 2
    assert len(finals) == 1

    final = finals[0]
    assert final.data["model"] == "claude-sonnet-4-6"
    u = final.data["usage"]
    assert u["input_tokens"] == 200  # 120 + 80
    assert u["output_tokens"] == 100  # 40 + 60
    assert u["cache_read_input_tokens"] == 200


@pytest.mark.asyncio
async def test_sdk_aggregate_used_when_present(monkeypatch):
    """Non-OAuth path: ResultMessage has the aggregate. Accumulator is
    ignored to avoid double-counting."""
    messages = [
        _FakeAssistantMessage(
            model="claude-sonnet-4-6",
            usage={"input_tokens": 100, "output_tokens": 50},
            text="…",
        ),
        _FakeResultMessage(
            model_usage={
                "claude-sonnet-4-6": {
                    "inputTokens": 100,
                    "outputTokens": 50,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                    "costUSD": 0.012,
                }
            },
            total_cost_usd=0.012,
        ),
    ]
    _patch_query(monkeypatch, messages)

    provider = ClaudeProvider()
    finals = []
    async for ev in provider.run(
        prompt="hi", system_prompt="", model="claude-sonnet-4-6"
    ):
        if ev.kind == "usage" and not ev.data.get("partial"):
            finals.append(ev)

    # SDK aggregate yielded — exactly one final, accumulator ignored.
    assert len(finals) == 1
    u = finals[0].data["usage"]
    # Normalized to snake_case.
    assert u["input_tokens"] == 100
    assert u["output_tokens"] == 50


@pytest.mark.asyncio
async def test_provider_flushes_accumulator_on_mid_stream_exception(monkeypatch):
    """When the SDK throws mid-stream (e.g. CLIConnectionError because
    the claude-code subprocess died), ResultMessage never fires and
    the accumulator hasn't been flushed. The exception handler must
    emit a final usage event from whatever was accumulated so the run
    still gets attributed tokens — otherwise OAuth runs silently
    drop usage on every transport error."""
    import services.llm.claude_provider as cp_module

    async def _flaky_query(*args, **kwargs):
        # Two AssistantMessages with real usage, then a connection error.
        yield _FakeAssistantMessage(
            model="claude-sonnet-4-6",
            usage={"input_tokens": 50, "output_tokens": 25},
            text="thinking…",
        )
        yield _FakeAssistantMessage(
            model="claude-sonnet-4-6",
            usage={"input_tokens": 70, "output_tokens": 30},
            text="more thinking…",
        )
        raise RuntimeError("ProcessTransport is not ready for writing")

    monkeypatch.setattr(cp_module, "query", _flaky_query)
    monkeypatch.setattr(cp_module, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(cp_module, "ResultMessage", _FakeResultMessage)
    monkeypatch.setattr(cp_module, "ClaudeAgentOptions", lambda **kw: kw)

    provider = ClaudeProvider()
    events = []
    async for ev in provider.run(
        prompt="hi", system_prompt="", model="claude-sonnet-4-6"
    ):
        events.append(ev)

    finals = [ev for ev in events if ev.kind == "usage" and not ev.data.get("partial")]
    errors = [ev for ev in events if ev.kind == "error"]

    assert len(finals) == 1, (
        f"Expected 1 flushed usage event after mid-stream crash, got {len(finals)}"
    )
    u = finals[0].data["usage"]
    assert u["input_tokens"] == 120  # 50 + 70
    assert u["output_tokens"] == 55  # 25 + 30
    # The error is still surfaced so the runner can log it.
    assert len(errors) == 1
    assert "ProcessTransport" in errors[0].data["message"]


@pytest.mark.asyncio
async def test_no_usage_at_all_yields_no_final_usage(monkeypatch):
    """When neither SDK aggregate nor AssistantMessage carry usage, the
    provider should not emit a phantom usage event."""
    messages = [
        _FakeAssistantMessage(model="claude-sonnet-4-6", usage={}, text="…"),
        _FakeResultMessage(model_usage=None, usage=None, total_cost_usd=None),
    ]
    _patch_query(monkeypatch, messages)

    provider = ClaudeProvider()
    usage_events = []
    async for ev in provider.run(
        prompt="hi", system_prompt="", model="claude-sonnet-4-6"
    ):
        if ev.kind == "usage":
            usage_events.append(ev)

    assert usage_events == []
