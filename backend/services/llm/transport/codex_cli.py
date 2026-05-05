"""Codex CLI transport — wraps `codex exec` to stream chat completions over OAuth.

The Codex CLI (https://github.com/openai/codex) is OpenAI's local CLI agent.
When the user runs `codex login`, it stores OAuth refresh tokens at
~/.codex/auth.json. The `codex exec` subcommand performs a one-shot,
non-interactive call we can drive headlessly.

Caveat: Codex CLI's headless interface is still evolving. We treat this as a
best-effort transport — if the JSON shape changes upstream, errors surface
through the normalized LLMEvent stream and the runner can fall back to skill-
only mode (no tool calls).
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from ..base import LLMEvent
from ._base import TransportError, spawn_jsonl

logger = logging.getLogger(__name__)


def _build_argv(model: str) -> list[str]:
    """Argv for a single Codex CLI invocation.

    --json asks for a structured event stream on stdout.
    --quiet suppresses interactive UI. The prompt is fed via stdin.
    """
    return ["codex", "exec", "--json", "--quiet", "--model", model]


def _format_prompt(system_prompt: str, user_prompt: str) -> str:
    """Codex CLI accepts a single prompt on stdin. We prepend the system
    prompt as a labeled section so the model still sees the directive.
    """
    return f"# System\n{system_prompt}\n\n# Task\n{user_prompt}\n"


async def stream(
    *,
    prompt: str,
    system_prompt: str,
    model: str,
    tools: list[dict] | None = None,
    timeout_seconds: int = 1800,
) -> AsyncIterator[LLMEvent]:
    """Drive Codex CLI and yield normalized LLMEvents.

    Tool support: Codex CLI exposes its OWN built-in tool surface (file
    operations, shell, etc.) and does not currently accept user-defined tool
    schemas the way Anthropic / OpenAI APIs do. For now we route Codex CLI as
    text-only — the runner sees no tool_call events and the agent must work
    via skill methodology + return-text-only.
    """
    if tools:
        logger.info("Codex CLI transport ignores user-defined tools (not supported).")

    argv = _build_argv(model)
    stdin_payload = _format_prompt(system_prompt, prompt)
    saw_text = False

    try:
        async for event in spawn_jsonl(
            argv,
            stdin_payload=stdin_payload,
            timeout_seconds=timeout_seconds,
        ):
            kind = event.get("type") or event.get("event")
            # Codex CLI's event shapes: "message", "delta", "tool_call",
            # "usage", "done". We accept multiple aliases since the schema
            # is in flux.
            if kind in ("message", "delta", "text"):
                text = event.get("text") or event.get("content") or ""
                if text:
                    saw_text = True
                    yield LLMEvent.text(text)
                continue
            if kind == "tool_call":
                fn = event.get("function") or {}
                name = fn.get("name") or event.get("name") or ""
                args = fn.get("arguments") or event.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                yield LLMEvent.tool_call(
                    tool_name=name,
                    tool_call_id=event.get("id") or name,
                    arguments=args,
                )
                continue
            if kind == "usage":
                usage = event.get("usage") or {}
                yield LLMEvent.usage(
                    model=model,
                    usage={
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    },
                )
                continue
            if kind in ("error", "exception"):
                yield LLMEvent.error(str(event.get("message") or event))
    except TransportError as e:
        yield LLMEvent.error(str(e))
    except Exception as e:
        logger.exception("Codex CLI transport failed")
        yield LLMEvent.error(str(e))

    if not saw_text:
        # Surface a hint when Codex returned no text — usually a config issue
        # (model id wrong, login expired). The runner shows the error to the
        # user instead of failing silently.
        yield LLMEvent.error(
            "Codex CLI produced no text. Try `codex login` again or pass a valid --model."
        )
    yield LLMEvent.done()
