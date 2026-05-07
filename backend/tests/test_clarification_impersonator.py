"""Unit tests for the request-clarification impersonator.

The impersonator must route through the parent agent's configured provider
(via the LLM factory), not call claude_agent_sdk.query() directly. These
tests pin that contract.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest


class _FakeEvent:
    def __init__(self, kind: str, data: dict | None = None):
        self.kind = kind
        self.data = data or {}


class _RecordingProvider:
    """Records run() calls so we can assert what the impersonator passed."""

    def __init__(self, reply_text: str = "Use the prep convention."):
        self.reply_text = reply_text
        self.calls: list[dict] = []
        self.capabilities = MagicMock(supports_mcp=False)

    async def run(self, **kwargs) -> AsyncIterator[_FakeEvent]:
        self.calls.append(kwargs)
        yield _FakeEvent("text", {"text": self.reply_text})


def _patch_agents(monkeypatch, *, provider_id: str, default_model: str = "test-model"):
    import services.agent.agents as agents

    monkeypatch.setattr(agents, "get_agent_provider", lambda _t: provider_id)
    monkeypatch.setattr(agents, "get_agent_default_model", lambda _t: default_model)
    monkeypatch.setattr(
        agents, "render_agent_system_prompt", lambda _t, **k: "[parent system]"
    )


@pytest.mark.asyncio
async def test_impersonator_routes_through_factory(monkeypatch):
    """When the parent agent's provider is openai, the impersonator must
    call the openai provider, not claude."""
    # The kebab-case directory means `from skills.request-clarification`
    # doesn't work — load the module via importlib instead.
    import services.skills.registry as reg

    skill = reg.get_skill("request-clarification")
    handler_path = reg._SKILLS_ROOT / skill.slug / "handler.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("rc_handler", handler_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Stub agents.get_agent_provider to return "openai" for the parent.
    _patch_agents(monkeypatch, provider_id="openai", default_model="gpt-x")

    provider = _RecordingProvider(reply_text="Use parquet.")
    monkeypatch.setattr(mod.llm_factory, "get_provider", lambda pid: provider)

    answer = await mod._run_impersonator(
        parent_agent_type="eda",
        parent_thought_stream="[recent stream]",
        question="What format should I write?",
        why_needed="To pick a writer",
        asker_agent_type="data_prep",
        parent_model=None,
    )

    assert answer == "Use parquet."
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["model"] == "gpt-x"
    assert "What format should I write?" in call["prompt"]
    # Impersonator must pass no tools — text-only.
    assert call["tools"] == []


@pytest.mark.asyncio
async def test_impersonator_uses_claude_when_parent_is_claude(monkeypatch):
    import services.skills.registry as reg

    skill = reg.get_skill("request-clarification")
    handler_path = reg._SKILLS_ROOT / skill.slug / "handler.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("rc_handler", handler_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    _patch_agents(monkeypatch, provider_id="claude", default_model="claude-x")

    provider = _RecordingProvider(reply_text="Yes, use parquet.")
    requested = {}

    def get_provider(pid):
        requested["id"] = pid
        return provider

    monkeypatch.setattr(mod.llm_factory, "get_provider", get_provider)

    await mod._run_impersonator(
        parent_agent_type="eda",
        parent_thought_stream="",
        question="ok?",
        why_needed="",
        asker_agent_type="data_prep",
        parent_model=None,
    )
    assert requested["id"] == "claude"


@pytest.mark.asyncio
async def test_impersonator_escalates_when_provider_unavailable(monkeypatch):
    """If the configured provider can't be resolved, escalate to the user."""
    import services.skills.registry as reg

    skill = reg.get_skill("request-clarification")
    handler_path = reg._SKILLS_ROOT / skill.slug / "handler.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("rc_handler", handler_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    _patch_agents(monkeypatch, provider_id="nonexistent")

    def boom(pid):
        raise KeyError(f"Unknown LLM provider '{pid}'")

    monkeypatch.setattr(mod.llm_factory, "get_provider", boom)

    answer = await mod._run_impersonator(
        parent_agent_type="eda",
        parent_thought_stream="",
        question="What target?",
        why_needed="",
        asker_agent_type="data_prep",
        parent_model=None,
    )
    assert answer.startswith("ESCALATE:")
    assert "What target?" in answer


@pytest.mark.asyncio
async def test_impersonator_escalates_on_timeout(monkeypatch):
    """An exceeded timeout should yield an ESCALATE: response, not crash."""
    import asyncio
    import services.skills.registry as reg

    skill = reg.get_skill("request-clarification")
    handler_path = reg._SKILLS_ROOT / skill.slug / "handler.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("rc_handler", handler_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    _patch_agents(monkeypatch, provider_id="claude", default_model="m")

    class _SlowProvider:
        capabilities = MagicMock(supports_mcp=False)

        async def run(self, **kwargs):
            await asyncio.sleep(120)
            yield _FakeEvent("done")

    monkeypatch.setattr(mod.llm_factory, "get_provider", lambda _: _SlowProvider())

    # Replace asyncio.timeout with a near-zero one to force the path.
    real_timeout = asyncio.timeout

    def fast_timeout(_seconds):
        return real_timeout(0.05)

    monkeypatch.setattr(mod.asyncio, "timeout", fast_timeout)

    answer = await mod._run_impersonator(
        parent_agent_type="eda",
        parent_thought_stream="",
        question="Q?",
        why_needed="",
        asker_agent_type="data_prep",
        parent_model=None,
    )
    assert answer.startswith("ESCALATE:")
    assert "Q?" in answer
