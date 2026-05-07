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

    if shutil.which("codex") and os.path.exists(
        os.path.expanduser("~/.codex/auth.json")
    ):
        return True
    return False


def _has_gemini() -> bool:
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return True
    import shutil

    if shutil.which("gemini") and os.path.exists(
        os.path.expanduser("~/.gemini/oauth_creds.json")
    ):
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
            pytest.skip(
                "set E2E_LITELLM_MODEL or one of GROQ_/DEEPSEEK_/MISTRAL_API_KEY"
            )

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
        {
            "role": "system",
            "content": "Use the `add` tool to compute. Then reply with the integer answer alone.",
        },
        {"role": "user", "content": "What is 14 + 28?"},
    ]

    # Round 1: expect tool_call.
    pending = []
    async for event in p.run(
        prompt="",
        system_prompt="",
        model=os.getenv("E2E_OPENAI_MODEL", "gpt-4o-mini"),
        tools=tools,
        max_turns=1,
        timeout_seconds=60,
        messages=messages,
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

    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call["tool_call_id"],
                    "type": "function",
                    "function": {
                        "name": "add",
                        "arguments": json.dumps(call["arguments"]),
                    },
                }
            ],
        }
    )
    messages.append(
        {"role": "tool", "tool_call_id": call["tool_call_id"], "content": str(result)}
    )

    final = ""
    async for event in p.run(
        prompt="",
        system_prompt="",
        model=os.getenv("E2E_OPENAI_MODEL", "gpt-4o-mini"),
        tools=tools,
        max_turns=1,
        timeout_seconds=60,
        messages=messages,
    ):
        if event.kind == "text":
            final += event.data.get("text", "")

    assert "42" in final, f"expected 42 in {final!r}"


# ---------------------------------------------------------------------------
# Dynamic tool activation via use-skill (knowledge skill brings in tools)
# ---------------------------------------------------------------------------


@requires("openai")
@pytest.mark.asyncio
async def test_dynamic_tool_activation_openai(tmp_path, monkeypatch):
    """End-to-end: a knowledge skill loaded via `use-skill` activates a
    capability skill that wasn't in the agent's base toolset, and the model
    calls it on the next turn.

    This is the OpenAI path because claude-agent-sdk bakes the toolset upfront
    and can't grow it mid-conversation; mid-run dynamic activation is a
    non-Claude feature today.
    """
    import json
    import shutil
    import uuid
    from pathlib import Path

    from services.llm.factory import get_provider
    from services.skills import (
        activate_tools,  # noqa: F401  (sanity export check)
        build_skill_entries,
        get_active_tools,
    )
    from services.skills import registry

    # ---- Build a tmp skills tree -----------------------------------------
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    # 1) A trivial capability skill the agent must NOT see in turn 1.
    say_magic = skills_root / "say-magic"
    say_magic.mkdir()
    (say_magic / "SKILL.md").write_text(
        "---\n"
        "name: say-magic\n"
        "description: Returns the magic phrase. Call only after loading magic-skill.\n"
        "when_to_use: when asked for the magic phrase\n"
        "version: '0.1'\n"
        "---\n"
    )
    (say_magic / "schema.yaml").write_text("type: object\nproperties: {}\n")
    (say_magic / "handler.py").write_text(
        "def create_handler(**ctx):\n"
        "    async def handler(args):\n"
        "        return {'content': [{'type': 'text', 'text': 'XYZZY-2718'}]}\n"
        "    return handler\n"
    )

    # 2) A knowledge skill that BRINGS IN say-magic when loaded via use-skill.
    magic = skills_root / "magic-skill"
    magic.mkdir()
    (magic / "SKILL.md").write_text(
        "---\n"
        "name: magic-skill\n"
        "description: Unlocks the magic-phrase tool.\n"
        "when_to_use: when the user wants the magic phrase\n"
        "version: '0.1'\n"
        "enables: [say-magic]\n"
        "---\n\n"
        "Once loaded, call the say-magic tool to get the phrase. Return its "
        "exact output to the user.\n"
    )

    # 3) Real use-skill — copied from the project so the handler / schema match.
    real_skills_root = Path(__file__).parent.parent / "skills"
    shutil.copytree(real_skills_root / "use-skill", skills_root / "use-skill")
    shutil.copytree(
        real_skills_root / "list-available-skills",
        skills_root / "list-available-skills",
    )

    monkeypatch.setattr(registry, "_SKILLS_ROOT", skills_root)
    registry.discover_skills.cache_clear()

    session_id = f"e2e-{uuid.uuid4().hex[:8]}"
    agent_id = "root"

    # use-skill needs the runner-managed loop to thread session+agent context
    # through, but `build_skill_entries` already does that via the mcp_bridge.
    # We only need a real agent_type for description rendering; "chat" works.
    agent_type = "chat"

    async def _publish_noop(*args, **kwargs):
        return None

    def _build_for(skills: list[str]) -> dict:
        return build_skill_entries(
            agent_type=agent_type,
            session_id=session_id,
            experiment_id="e2e-experiment",
            stage="chat",
            depth=0,
            publish_fn=_publish_noop,
            sandbox_config={},
            model="gpt-4o-mini",
            instructions="",
            agent_models={},
            agent_id=agent_id,
            parent_agent_id=None,
            agent_skills_override=skills,
        )

    def _entries_to_specs(entries: dict) -> list[dict]:
        return [
            {
                "name": slug,
                "description": entry["description"],
                "input_schema": entry["input_schema"]
                or {"type": "object", "properties": {}},
            }
            for slug, entry in entries.items()
        ]

    # ---- Drive a runner-style turn loop ----------------------------------
    p = get_provider("openai")
    model_id = os.getenv("E2E_OPENAI_MODEL", "gpt-4o-mini")

    base_skills = ["list-available-skills", "use-skill"]
    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You can load specialized skills with the `use-skill` tool. "
                "Loading a skill may grant you new tools on the next turn — "
                "watch the response for a 'Tools enabled by this skill' "
                "section. To answer the user's request, first load the skill "
                "named 'magic-skill', then call the tool it enables. "
                "Reply with only the phrase that tool returns, nothing else."
            ),
        },
        {"role": "user", "content": "What is the magic phrase?"},
    ]

    cached_skills: list[str] | None = None
    specs: list[dict] = []
    handlers: dict = {}

    saw_use_skill = False
    saw_say_magic = False
    final_text = ""

    try:
        for turn in range(5):  # generous cap; expect 3 turns
            # Mirror runner._resolve_effective_skills: base ∪ activations,
            # capability-only, deterministic order.
            extras = sorted(
                s
                for s in get_active_tools(session_id, agent_id)
                if s not in base_skills
            )
            effective = list(base_skills) + extras
            if effective != cached_skills:
                entries = _build_for(effective)
                specs = _entries_to_specs(entries)
                handlers = {slug: e["handler"] for slug, e in entries.items()}
                cached_skills = effective

            pending: list[dict] = []
            assistant_text_parts: list[str] = []

            async for event in p.run(
                prompt="",
                system_prompt="",
                model=model_id,
                tools=specs,
                max_turns=1,
                timeout_seconds=60,
                messages=messages,
            ):
                if event.kind == "text":
                    assistant_text_parts.append(event.data.get("text", "") or "")
                elif event.kind == "tool_call":
                    pending.append(
                        {
                            "id": event.data.get("tool_call_id") or f"call_{turn}",
                            "name": event.data.get("tool_name", ""),
                            "args": event.data.get("arguments", {}) or {},
                        }
                    )
                elif event.kind == "error":
                    pytest.fail(f"provider error: {event.data.get('message')}")

            assistant_msg: dict = {
                "role": "assistant",
                "content": "".join(assistant_text_parts) or None,
            }
            if pending:
                assistant_msg["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c["args"]),
                        },
                    }
                    for c in pending
                ]
            messages.append(assistant_msg)

            if not pending:
                final_text = "".join(assistant_text_parts)
                break

            for call in pending:
                if call["name"] == "use-skill":
                    saw_use_skill = True
                if call["name"] == "say-magic":
                    saw_say_magic = True
                handler = handlers.get(call["name"])
                if handler is None:
                    result_text = f"Unknown tool: {call['name']}"
                else:
                    result = await handler(call["args"])
                    parts = []
                    for item in (result or {}).get("content", []) or []:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                    result_text = "\n".join(p for p in parts if p) or "(no output)"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result_text,
                    }
                )
    finally:
        registry.discover_skills.cache_clear()

    # ---- Assertions ------------------------------------------------------
    assert saw_use_skill, (
        "Model never called `use-skill`; it should have loaded magic-skill first."
    )
    # The activation should have happened — say-magic must be in the active set.
    assert "say-magic" in get_active_tools(session_id, agent_id), (
        "use-skill load did not activate `say-magic` for this (session, agent)."
    )
    assert saw_say_magic, (
        "Model never called `say-magic` after activation — the dynamic tool "
        "did not propagate into the next turn's tool list."
    )
    assert "XYZZY-2718" in final_text, (
        f"Model didn't return the magic phrase from the activated tool. "
        f"Final reply: {final_text!r}"
    )
