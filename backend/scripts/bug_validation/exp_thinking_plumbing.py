"""B3 — `thinking_level` must reach the provider as kwargs.

Pre-fix: the runner computed `thinking_level` from agent YAML + UI
override at runner.py:1058 and then never passed it to `_drive_provider`,
so the UI thinking-level picker had zero effect.

Post-fix: `_drive_provider(thinking_level=...)` calls
`to_provider_config(provider_id, level, model_id=model)` and spreads the
result into `provider.run(**)`. OpenAI consumes `reasoning_effort`; the
catch-all `to_provider_config` also handles Claude (`thinking={...}`)
and Gemini (`thinking_config={...}`) shapes for when those providers
gain the wiring.

Run:
    cd backend && .venv/bin/python scripts/bug_validation/exp_thinking_plumbing.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import AsyncIterator
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class _Event:
    def __init__(self, kind: str, data: dict | None = None):
        self.kind = kind
        self.data = data or {}


class _StubProvider:
    """Records what the runner passed in."""

    def __init__(self):
        self.calls: list[dict] = []
        self.capabilities = MagicMock(supports_mcp=False)

    async def run(self, **kwargs) -> AsyncIterator[_Event]:
        self.calls.append(kwargs)
        yield _Event("text", {"text": "stub reply"})


async def _drive(level: str | None, model: str, provider_id: str) -> dict:
    from services.agent import runner
    from services.agent import agents as agents_mod

    provider = _StubProvider()

    # Patch the seams _drive_provider depends on without touching the
    # rest of the runner module.
    runner.llm_factory.get_provider = lambda _id: provider
    runner.build_skill_entries = lambda **kw: {}
    agents_mod.get_agent_skills = lambda _t: []
    agents_mod.get_skill_for_agent = lambda agent_type, slug: None

    async def _no_record(*a, **k):
        pass

    runner.record_llm_usage = _no_record

    async def _publish(*a, **k):
        pass

    await runner._drive_provider(
        provider_id=provider_id,
        prompt="hi",
        system_prompt="sys",
        model=model,
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
        publish=_publish,
        agent_span=MagicMock(),
        thinking_level=level,
    )
    return provider.calls[0]


async def main() -> int:
    # 1. OpenAI + "high" → reasoning_effort=high
    call = await _drive(level="high", model="gpt-5.5-mini", provider_id="openai")
    assert call.get("reasoning_effort") == "high", call
    print(f"  openai gpt-5.5 high  -> reasoning_effort={call['reasoning_effort']!r}")

    # 2. OpenAI + "off" on GPT-5 (no minimal-floor logic) → reasoning_effort=minimal
    call = await _drive(level="off", model="gpt-5", provider_id="openai")
    assert call.get("reasoning_effort") == "minimal", call
    print(f"  openai gpt-5 off     -> reasoning_effort={call['reasoning_effort']!r}")

    # 3. No level passed → no provider kwargs added.
    call = await _drive(level=None, model="gpt-5", provider_id="openai")
    assert "reasoning_effort" not in call, call
    print("  openai no level      -> (no reasoning_effort kwarg)")

    # 4. Claude path receives `thinking={...}` config (Claude provider currently
    #    ignores it; this script only verifies the runner-level plumbing).
    call = await _drive(level="medium", model="claude-sonnet-4-6", provider_id="claude")
    thinking = call.get("thinking")
    assert isinstance(thinking, dict) and thinking.get("type") == "enabled", call
    print(f"  claude sonnet medium -> thinking={thinking}")

    print("PASS — thinking_level reaches provider.run() for every provider")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
