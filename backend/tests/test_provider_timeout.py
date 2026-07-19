"""Wall-clock timeout enforcement on provider LLM calls (issue #95).

A stalled provider HTTP call used to hang the session's background task
forever: the runner's outer `asyncio.timeout` was deliberately removed
(the sandbox timeout only bounds tool execution) and every provider
accepted `timeout_seconds` but discarded it. These tests pin the fix:

  * `enforce_wall_clock` raises builtin TimeoutError once the budget is
    exceeded (and is a no-op for falsy/non-positive budgets);
  * OpenAI / Gemini / LiteLLM providers abort a hung SDK call within the
    budget and let TimeoutError propagate (the runner's TimeoutError
    handler publishes `agent_timeout` and frees the session task);
  * the Claude provider — whose SDK runs the tool loop internally, so it
    must NOT be wrapped wholesale — threads the budget into the CLI env
    as API_TIMEOUT_MS, bounding each provider HTTP request only.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from services.llm.base import enforce_wall_clock

# Small enough that a hung call aborts fast; big enough not to flake.
_BUDGET = 0.1
# A "hung" provider call: far beyond the budget.
_HANG = 30.0


async def _slow(value="never"):
    await asyncio.sleep(_HANG)
    return value


class TestEnforceWallClock:
    @pytest.mark.asyncio
    async def test_raises_builtin_timeout_error_within_budget(self):
        start = time.monotonic()
        with pytest.raises(TimeoutError, match="wall-clock timeout"):
            await enforce_wall_clock(_slow(), _BUDGET, provider="openai")
        assert time.monotonic() - start < 5

    @pytest.mark.asyncio
    async def test_fast_call_passes_through(self):
        async def _fast():
            return 42

        assert await enforce_wall_clock(_fast(), _BUDGET, provider="x") == 42

    @pytest.mark.asyncio
    async def test_falsy_or_negative_budget_disables_cap(self):
        async def _fast():
            return "ok"

        assert await enforce_wall_clock(_fast(), None, provider="x") == "ok"
        assert await enforce_wall_clock(_fast(), 0, provider="x") == "ok"
        assert await enforce_wall_clock(_fast(), -5, provider="x") == "ok"


class TestOpenAIProviderTimeout:
    @pytest.mark.asyncio
    async def test_hung_responses_call_raises_within_budget(self, monkeypatch):
        from services.llm import openai_provider as op

        monkeypatch.setattr(
            op,
            "resolve_credentials",
            lambda _n: MagicMock(token="fake", mode="api_key", extra={}),
        )
        provider = op.OpenAIProvider()

        async def _hung_create(**kwargs):
            await asyncio.sleep(_HANG)

        fake_client = MagicMock()
        fake_client.with_options.return_value = fake_client
        fake_client.responses.create = _hung_create
        provider._client = fake_client

        start = time.monotonic()
        with pytest.raises(TimeoutError, match="openai"):
            async for _ in provider.run(
                prompt="p",
                system_prompt="s",
                model="gpt-5",
                timeout_seconds=_BUDGET,
            ):
                pass
        assert time.monotonic() - start < 5
        # The per-request SDK timeout was threaded too.
        fake_client.with_options.assert_called_once_with(timeout=_BUDGET)


class TestGeminiProviderTimeout:
    @pytest.mark.asyncio
    async def test_hung_generate_content_raises_within_budget(self, monkeypatch):
        pytest.importorskip("google.genai")
        from services.llm import gemini_provider as gp

        monkeypatch.setattr(
            gp,
            "resolve_credentials",
            lambda _n: MagicMock(token="fake", mode="api_key", extra={}),
        )
        provider = gp.GeminiProvider()

        async def _hung_generate(**kwargs):
            await asyncio.sleep(_HANG)

        fake_client = MagicMock()
        fake_client.aio.models.generate_content = _hung_generate
        provider._client = fake_client

        start = time.monotonic()
        with pytest.raises(TimeoutError, match="gemini"):
            async for _ in provider.run(
                prompt="p",
                system_prompt="s",
                model="gemini-2.5-flash",
                timeout_seconds=_BUDGET,
            ):
                pass
        assert time.monotonic() - start < 5


class TestLiteLLMProviderTimeout:
    @pytest.mark.asyncio
    async def test_hung_acompletion_raises_within_budget(self, monkeypatch):
        from services.llm import litellm_provider as lp

        monkeypatch.setattr(
            lp,
            "resolve_credentials",
            lambda _n: MagicMock(token="fake", mode="api_key", extra={}),
        )
        provider = lp.LiteLLMProvider()

        captured: dict = {}

        async def _hung_acompletion(**kwargs):
            captured.update(kwargs)
            await asyncio.sleep(_HANG)

        fake_litellm = MagicMock()
        fake_litellm.acompletion = _hung_acompletion
        provider._litellm = fake_litellm

        start = time.monotonic()
        with pytest.raises(TimeoutError, match="litellm"):
            async for _ in provider.run(
                prompt="p",
                system_prompt="s",
                model="groq/llama-3.3-70b",
                timeout_seconds=_BUDGET,
            ):
                pass
        assert time.monotonic() - start < 5
        # The per-attempt transport timeout still reaches litellm itself.
        assert captured["timeout"] == _BUDGET


class TestClaudeProviderTimeoutEnv:
    @pytest.mark.asyncio
    async def test_api_timeout_ms_threaded_into_cli_env(self, monkeypatch):
        import services.llm.claude_provider as cp

        captured: dict = {}

        async def _fake_query(*args, **kwargs):
            captured["options"] = kwargs.get("options")
            return
            yield  # pragma: no cover — makes this an async generator

        monkeypatch.setattr(cp, "query", _fake_query)
        monkeypatch.setattr(cp, "ClaudeAgentOptions", lambda **kw: kw)

        provider = cp.ClaudeProvider()
        events = [
            ev
            async for ev in provider.run(
                prompt="p",
                system_prompt="s",
                model="claude-sonnet-4-6",
                timeout_seconds=120,
                env={"CLAUDE_CODE_OAUTH_TOKEN": "tok"},
            )
        ]

        env = captured["options"]["env"]
        assert env["API_TIMEOUT_MS"] == "120000"
        # Caller-supplied env vars survive the merge.
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
        assert events[-1].kind == "done"

    @pytest.mark.asyncio
    async def test_caller_api_timeout_ms_wins(self, monkeypatch):
        import services.llm.claude_provider as cp

        captured: dict = {}

        async def _fake_query(*args, **kwargs):
            captured["options"] = kwargs.get("options")
            return
            yield  # pragma: no cover

        monkeypatch.setattr(cp, "query", _fake_query)
        monkeypatch.setattr(cp, "ClaudeAgentOptions", lambda **kw: kw)

        provider = cp.ClaudeProvider()
        async for _ in provider.run(
            prompt="p",
            system_prompt="s",
            model="claude-sonnet-4-6",
            timeout_seconds=120,
            env={"API_TIMEOUT_MS": "5000"},
        ):
            pass

        assert captured["options"]["env"]["API_TIMEOUT_MS"] == "5000"
