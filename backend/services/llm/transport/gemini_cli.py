"""Gemini CLI transport — wraps `gemini` for OAuth-backed Gemini calls.

The Gemini CLI (https://github.com/google-gemini/gemini-cli) stores OAuth
state under ~/.gemini/. We invoke it in non-interactive mode with --prompt
and parse its JSON output.

Like the Codex CLI transport, this is text-only — Gemini CLI does not yet
accept arbitrary user-defined function declarations through its non-interactive
interface. Tool calls are dispatched in the runner using skill handlers.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from ..base import LLMEvent
from ._base import TransportError, spawn_jsonl

logger = logging.getLogger(__name__)


def _build_argv(model: str, prompt_text: str) -> list[str]:
    """Argv for a single Gemini CLI invocation.

    --output json requests structured output. --prompt feeds the prompt
    inline; --model selects the model. --quiet suppresses banner output.
    """
    return [
        "gemini",
        "--model",
        model,
        "--output",
        "json",
        "--prompt",
        prompt_text,
    ]


def _format_prompt(system_prompt: str, user_prompt: str) -> str:
    return f"[System]\n{system_prompt}\n\n[Task]\n{user_prompt}\n"


async def stream(
    *,
    prompt: str,
    system_prompt: str,
    model: str,
    tools: list[dict] | None = None,
    timeout_seconds: int = 1800,
) -> AsyncIterator[LLMEvent]:
    """Drive Gemini CLI and yield normalized LLMEvents (text-only)."""
    if tools:
        logger.info("Gemini CLI transport ignores user-defined tools (not supported).")

    prompt_text = _format_prompt(system_prompt, prompt)
    argv = _build_argv(model, prompt_text)
    saw_text = False

    try:
        async for event in spawn_jsonl(argv, timeout_seconds=timeout_seconds):
            text = event.get("text") or event.get("response") or event.get("content")
            if isinstance(text, str) and text:
                saw_text = True
                yield LLMEvent.text(text)
                continue
            usage = event.get("usage") or event.get("usage_metadata")
            if usage:
                yield LLMEvent.usage(
                    model=model,
                    usage={
                        "input_tokens": usage.get("prompt_token_count", 0)
                        or usage.get("input_tokens", 0),
                        "output_tokens": usage.get("candidates_token_count", 0)
                        or usage.get("output_tokens", 0),
                    },
                )
                continue
            if event.get("error"):
                yield LLMEvent.error(str(event.get("error")))
    except TransportError as e:
        yield LLMEvent.error(str(e))
    except Exception as e:
        logger.exception("Gemini CLI transport failed")
        yield LLMEvent.error(str(e))

    if not saw_text:
        yield LLMEvent.error(
            "Gemini CLI produced no text. Try `gemini auth login` or check the model name."
        )
    yield LLMEvent.done()
