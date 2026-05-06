"""End-to-end smoke tests for each LLM provider.

These tests make REAL network calls — they are opt-in. To run:

    RUN_LLM_E2E=1 pytest backend/tests/test_providers_e2e.py -v

Each provider is independently gated. A test is skipped if its credentials
aren't present, so you can run a subset (e.g. only Claude) without having to
configure all four providers.

Goals:
  1. Verify the auth resolver picks the right transport per environment.
  2. Verify each provider can complete a one-shot text call.
  3. Verify the runner's tool-dispatch loop works for a non-Claude provider
     (call a local skill handler — no Modal sandbox needed).

If you're adding a new provider, add a `_<provider>_keys()` helper, mark its
test with @requires(<provider>), and follow the existing pattern.
"""

from __future__ import annotations

import os
import pytest

E2E_FLAG = "RUN_LLM_E2E"


def _e2e_enabled() -> bool:
    return os.getenv(E2E_FLAG) == "1"


def _has_claude() -> bool:
    return bool(os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY"))


def _has_openai() -> bool:
    if os.getenv("OPENAI_API_KEY"):
        return True
    # Codex CLI OAuth (~/.codex/auth.json + `codex` on PATH)
    import shutil
    if shutil.which("codex") and os.path.exists(os.path.expanduser("~/.codex/auth.json")):
        return True
    return False


def _has_gemini() -> bool:
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return True
    import shutil
    if shutil.which("gemini") and os.path.exists(os.path.expanduser("~/.gemini/oauth_creds.json")):
        return True
    return False


def _has_litellm() -> bool:
    return bool(
        os.getenv("GROQ_API_KEY")
        or os.getenv("MISTRAL_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("TOGETHER_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
    )


def requires(provider: str):
    """Skip marker that only runs the test when E2E_FLAG=1 AND the relevant
    credentials are configured. Failing either guard SKIPs (not fails)."""
    enabled = {
        "claude": _has_claude,
        "openai": _has_openai,
        "gemini": _has_gemini,
        "litellm": _has_litellm,
    }[provider]
    return pytest.mark.skipif(
        not _e2e_enabled() or not enabled(),
        reason=f"set {E2E_FLAG}=1 and configure {provider} credentials to enable",
    )


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Per-provider one-shot calls
# ---------------------------------------------------------------------------


async def _collect_text(provider, *, prompt: str, system: str, model: str) -> str:
    """Drive provider.run() to completion and return concatenated text."""
    text = ""
    async for event in provider.run(
        prompt=prompt,
        system_prompt=system,
        model=model,
        tools=[],
        mcp_servers={},
        max_turns=1,
        timeout_seconds=60,
    ):
        if event.kind == "text":
            text += event.data.get("text", "")
        elif event.kind == "error":
            pytest.fail(f"provider error: {event.data.get('message')}")
    return text


@requires("claude")
@pytest.mark.asyncio
async def test_claude_oneshot():
    from services.llm.factory import get_provider

    p = get_provider("claude")
    text = await _collect_text(
        p,
        prompt="Reply with exactly: PONG",
        system="You are a smoke test. Do exactly what you are asked.",
        model=os.getenv("E2E_CLAUDE_MODEL", "claude-sonnet-4-6"),
    )
    assert "PONG" in text.upper(), f"got: {text!r}"


@requires("openai")
@pytest.mark.asyncio
async def test_openai_oneshot():
    from services.llm.factory import get_provider

    p = get_provider("openai")
    text = await _collect_text(
        p,
        prompt="Reply with exactly: PONG",
        system="You are a smoke test. Do exactly what you are asked.",
        model=os.getenv("E2E_OPENAI_MODEL", "gpt-4o-mini"),
    )
    assert "PONG" in text.upper(), f"got: {text!r}"


@requires("gemini")
@pytest.mark.asyncio
async def test_gemini_oneshot():
    from services.llm.factory import get_provider

    p = get_provider("gemini")
    text = await _collect_text(
        p,
        prompt="Reply with exactly: PONG",
        system="You are a smoke test.",
        model=os.getenv("E2E_GEMINI_MODEL", "gemini-2.0-flash-exp"),
    )
    assert "PONG" in text.upper(), f"got: {text!r}"


@requires("litellm")
@pytest.mark.asyncio
async def test_litellm_oneshot():
    from services.llm.factory import get_provider

    p = get_provider("litellm")
    # Pick whichever backend the user wired. The model id encodes the backend.
    model = os.getenv("E2E_LITELLM_MODEL")
    if not model:
        if os.getenv("GROQ_API_KEY"):
            model = "groq/llama-3.3-70b-versatile"
        elif os.getenv("DEEPSEEK_API_KEY"):
            model = "deepseek/deepseek-chat"
        elif os.getenv("MISTRAL_API_KEY"):
            model = "mistral/mistral-large-latest"
        else:
            pytest.skip("set E2E_LITELLM_MODEL or one of GROQ_/DEEPSEEK_/MISTRAL_API_KEY")

    text = await _collect_text(
        p,
        prompt="Reply with exactly: PONG",
        system="You are a smoke test.",
        model=model,
    )
    assert "PONG" in text.upper(), f"got: {text!r}"


# ---------------------------------------------------------------------------
# Tool-call loop on a non-Claude provider
# ---------------------------------------------------------------------------


@requires("openai")
@pytest.mark.asyncio
async def test_openai_tool_call_loop():
    """Verify the runner-managed tool loop completes one round-trip with a
    non-Claude provider. Uses a tiny in-test handler — no DB / Modal needed."""
    from services.llm.factory import get_provider

    p = get_provider("openai")

    # Single-tool spec: a deterministic adder.
    tools = [
        {
            "name": "add",
            "description": "Return a+b.",
            "input_schema": {
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
        }
    ]

    messages = [
        {"role": "system", "content": "Use the `add` tool to compute. Then reply with the integer answer alone."},
        {"role": "user", "content": "What is 14 + 28?"},
    ]

    # Round 1: expect tool_call.
    pending = []
    async for event in p.run(
        prompt="", system_prompt="", model=os.getenv("E2E_OPENAI_MODEL", "gpt-4o-mini"),
        tools=tools, max_turns=1, timeout_seconds=60, messages=messages,
    ):
        if event.kind == "tool_call":
            pending.append(event.data)

    assert pending, "expected the model to call `add`"
    call = pending[0]
    assert call["tool_name"] == "add"

    # Run the "tool".
    a, b = call["arguments"].get("a", 0), call["arguments"].get("b", 0)
    result = a + b

    # Round 2: append assistant + tool_result, ask for the final reply.
    import json
    messages.append({
        "role": "assistant", "content": None,
        "tool_calls": [{
            "id": call["tool_call_id"], "type": "function",
            "function": {"name": "add", "arguments": json.dumps(call["arguments"])},
        }],
    })
    messages.append({"role": "tool", "tool_call_id": call["tool_call_id"], "content": str(result)})

    final = ""
    async for event in p.run(
        prompt="", system_prompt="", model=os.getenv("E2E_OPENAI_MODEL", "gpt-4o-mini"),
        tools=tools, max_turns=1, timeout_seconds=60, messages=messages,
    ):
        if event.kind == "text":
            final += event.data.get("text", "")

    assert "42" in final, f"expected 42 in {final!r}"
