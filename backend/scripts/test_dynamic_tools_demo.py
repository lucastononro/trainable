"""Manual demo: knowledge skill loaded via use-skill activates a tool mid-run.

Run from `backend/`:

    set -a && source ../.env && set -a
    .venv/bin/python scripts/test_dynamic_tools_demo.py

Set OPENAI_API_KEY (or any other LLM provider env you want to drive). The
default is OpenAI because claude-agent-sdk bakes its toolset upfront and
can't grow it inside one run; the runner-managed loop on OpenAI/Gemini/
LiteLLM is where dynamic activation actually takes effect per turn.

What it does:
  1. Builds a tmp skills tree:
       - say-magic         (capability) returns 'XYZZY-2718'
       - magic-skill       (knowledge)  enables: [say-magic]
       - use-skill, list-available-skills (copied from real skills)
  2. Points the registry at the tmp tree.
  3. Drives a runner-style turn loop against the chosen provider.
  4. Prints which tools are visible each turn, which the model picked,
     and the final answer.

Pass --provider {openai,gemini,litellm} to switch providers (default: openai).
Pass --model <id> to override the model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

# Make `services.*` importable when run as a script.
sys.path.insert(0, str(Path(__file__).parent.parent))


def _color(s: str, code: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


GREEN = lambda s: _color(s, "32")  # noqa: E731
YELLOW = lambda s: _color(s, "33")  # noqa: E731
CYAN = lambda s: _color(s, "36")  # noqa: E731
DIM = lambda s: _color(s, "2")  # noqa: E731
BOLD = lambda s: _color(s, "1")  # noqa: E731


def _build_skills_tree(root: Path) -> None:
    """Materialize the demo skills tree under `root/skills/`."""
    skills_root = root / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    say_magic = skills_root / "say-magic"
    say_magic.mkdir(exist_ok=True)
    (say_magic / "SKILL.md").write_text(
        "---\n"
        "name: say-magic\n"
        "description: Returns the magic phrase. Only call after loading magic-skill.\n"
        "when_to_use: when the user asks for the magic phrase\n"
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

    magic = skills_root / "magic-skill"
    magic.mkdir(exist_ok=True)
    (magic / "SKILL.md").write_text(
        "---\n"
        "name: magic-skill\n"
        "description: Unlocks the magic-phrase tool.\n"
        "when_to_use: when the user wants the magic phrase\n"
        "version: '0.1'\n"
        "enables: [say-magic]\n"
        "---\n\n"
        "Once loaded, call the say-magic tool to get the phrase. "
        "Return its exact output to the user.\n"
    )

    real_skills_root = Path(__file__).parent.parent / "skills"
    for slug in ("use-skill", "list-available-skills"):
        target = skills_root / slug
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(real_skills_root / slug, target)


async def _drive(provider_id: str, model: str) -> int:
    from services.llm.factory import get_provider
    from services.skills import build_skill_entries, get_active_tools
    from services.skills import registry

    p = get_provider(provider_id)

    session_id = f"demo-{uuid.uuid4().hex[:8]}"
    agent_id = "root"
    agent_type = "chat"

    base_skills = ["list-available-skills", "use-skill"]

    async def _publish_noop(*args, **kwargs):
        return None

    def _build_for(skills: list[str]) -> dict:
        return build_skill_entries(
            agent_type=agent_type,
            session_id=session_id,
            experiment_id="demo-experiment",
            stage="chat",
            depth=0,
            publish_fn=_publish_noop,
            sandbox_config={},
            model=model,
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

    print(BOLD(CYAN(f"\n=== Dynamic-tool activation demo ({provider_id}/{model}) ===")))
    print(DIM(f"session_id={session_id} agent_id={agent_id}"))

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

    cached: list[str] | None = None
    specs: list[dict] = []
    handlers: dict = {}
    final_text = ""

    try:
        for turn in range(5):
            extras = sorted(
                s
                for s in get_active_tools(session_id, agent_id)
                if s not in base_skills
            )
            effective = list(base_skills) + extras
            if effective != cached:
                entries = _build_for(effective)
                specs = _entries_to_specs(entries)
                handlers = {slug: e["handler"] for slug, e in entries.items()}
                cached = effective

            print(BOLD(f"\n— Turn {turn + 1} —"))
            print(f"  active extras:     {YELLOW(str(extras) or '[]')}")
            print(f"  tools sent to LLM: {[s['name'] for s in specs]}")

            pending: list[dict] = []
            text_parts: list[str] = []

            async for event in p.run(
                prompt="",
                system_prompt="",
                model=model,
                tools=specs,
                max_turns=1,
                timeout_seconds=60,
                messages=messages,
            ):
                if event.kind == "text":
                    text_parts.append(event.data.get("text", "") or "")
                elif event.kind == "tool_call":
                    pending.append(
                        {
                            "id": event.data.get("tool_call_id") or f"call_{turn}",
                            "name": event.data.get("tool_name", ""),
                            "args": event.data.get("arguments", {}) or {},
                        }
                    )
                elif event.kind == "error":
                    print(YELLOW(f"  PROVIDER ERROR: {event.data.get('message')}"))
                    return 2

            if text_parts:
                joined = "".join(text_parts).strip()
                if joined:
                    print(f"  model text:        {GREEN(joined[:240])}")

            assistant_msg: dict = {
                "role": "assistant",
                "content": "".join(text_parts) or None,
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
                final_text = "".join(text_parts)
                break

            for call in pending:
                print(f"  -> tool_call:      {CYAN(call['name'])} args={call['args']}")
                handler = handlers.get(call["name"])
                if handler is None:
                    result_text = f"Unknown tool: {call['name']}"
                    print(YELLOW(f"     (no handler for {call['name']})"))
                else:
                    result = await handler(call["args"])
                    parts = []
                    for item in (result or {}).get("content", []) or []:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                    result_text = "\n".join(p for p in parts if p) or "(no output)"
                preview = result_text.replace("\n", " ⏎ ")[:180]
                print(f"  <- tool_result:    {DIM(preview)}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result_text,
                    }
                )
    finally:
        registry.discover_skills.cache_clear()

    print(BOLD("\n=== Result ==="))
    active = get_active_tools(session_id, agent_id)
    print(f"  active tools at end: {sorted(active)}")
    print(f"  final reply:         {GREEN(repr(final_text))}")

    ok = "say-magic" in active and "XYZZY-2718" in final_text
    if ok:
        print(BOLD(GREEN("\nPASS: dynamic activation worked end-to-end.\n")))
        return 0
    print(BOLD(YELLOW("\nFAIL: see traces above.\n")))
    return 1


def _patch_registry_root(root: Path) -> None:
    from services.skills import registry

    registry._SKILLS_ROOT = root / "skills"
    registry.discover_skills.cache_clear()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--provider",
        default="openai",
        choices=["openai", "gemini", "litellm"],
        help="LLM provider (default: openai). Claude is excluded — claude-agent-sdk "
        "bakes the toolset upfront, so dynamic activation only takes effect "
        "across separate runs, not within one.",
    )
    ap.add_argument(
        "--model",
        default=None,
        help="Model id (defaults: openai=gpt-4o-mini, gemini=gemini-2.0-flash-exp).",
    )
    args = ap.parse_args()

    default_model = {
        "openai": "gpt-4o-mini",
        "gemini": "gemini-2.0-flash-exp",
        "litellm": os.getenv("E2E_LITELLM_MODEL", "groq/llama-3.3-70b-versatile"),
    }[args.provider]
    model = args.model or default_model

    with tempfile.TemporaryDirectory(prefix="trainable-skills-demo-") as tmp:
        tmp_path = Path(tmp)
        _build_skills_tree(tmp_path)
        _patch_registry_root(tmp_path)
        return asyncio.run(_drive(args.provider, model))


if __name__ == "__main__":
    raise SystemExit(main())
