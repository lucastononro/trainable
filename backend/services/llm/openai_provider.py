"""OpenAI provider — chat completions with multi-turn tool calling.

Drives a full agent loop internally when given a ``tool_dispatch``
callback: each round, the model emits tool_calls, the runner-supplied
dispatcher executes them, results feed back into ``messages``, repeat
until the model stops calling tools or ``max_turns`` is hit.

Without ``tool_dispatch`` the provider stops at the first tool boundary
(useful for unit tests or callers that drive the loop themselves).

Imports openai lazily so the package's mere presence in the registry
doesn't require the SDK to be installed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Awaitable, Callable

from .base import LLMEvent, LLMProvider, ProviderCapabilities
from .thinking import to_provider_config

logger = logging.getLogger(__name__)


# Signature of the runner-supplied tool dispatcher. Returns a string
# (the tool's textual result) — providers paste it into the next turn
# as the tool message content.
ToolDispatch = Callable[[str, str, dict], Awaitable[str]]
# args are (tool_call_id, tool_name, arguments)


def _make_client():
    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        raise RuntimeError("openai SDK not installed — `pip install openai`") from e
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY env var not set")
    return AsyncOpenAI(api_key=api_key)


def _to_openai_tool(name: str, description: str, input_schema: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema or {"type": "object", "properties": {}},
        },
    }


class OpenAIProvider(LLMProvider):
    capabilities = ProviderCapabilities(
        name="openai",
        supports_subagents=False,
        supports_mcp=False,
        # Implicit prompt caching exists; we don't surface a knob for it.
        supports_prompt_cache=False,
        supports_streaming=True,
        default_model="gpt-5",
    )

    def __init__(self):
        self._client = None

    def _client_or_raise(self):
        if self._client is None:
            self._client = _make_client()
        return self._client

    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None = None,
        mcp_servers: dict | None = None,  # noqa: ARG002 — OpenAI doesn't speak MCP
        max_turns: int = 30,
        timeout_seconds: int = 1800,  # noqa: ARG002 — runner handles outer timeout
        tool_dispatch: ToolDispatch | None = None,
        thinking_level: str | None = None,
        **kwargs: Any,  # noqa: ARG002 — accept provider_specific extras
    ) -> AsyncIterator[LLMEvent]:
        client = self._client_or_raise()

        oai_tools = [
            _to_openai_tool(
                t["name"], t.get("description", ""), t.get("input_schema") or {}
            )
            for t in (tools or [])
        ]

        # OpenAI's reasoning_effort is set per request; resolve once.
        thinking_kwargs = to_provider_config("openai", thinking_level, model_id=model)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            for turn in range(max_turns):
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=oai_tools or None,
                    stream=False,
                    **thinking_kwargs,
                )
                choice = resp.choices[0]
                msg = choice.message
                tool_calls = list(getattr(msg, "tool_calls", None) or [])

                # Yield assistant text, if any.
                if msg.content:
                    yield LLMEvent.text(msg.content)

                # Append the assistant turn to history. Must include
                # tool_calls so the API can correlate the next round's
                # tool messages.
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        **(
                            {
                                "tool_calls": [
                                    {
                                        "id": tc.id,
                                        "type": "function",
                                        "function": {
                                            "name": tc.function.name,
                                            "arguments": tc.function.arguments,
                                        },
                                    }
                                    for tc in tool_calls
                                ]
                            }
                            if tool_calls
                            else {}
                        ),
                    }
                )

                # Surface usage for cost tracking.
                if resp.usage:
                    yield LLMEvent.usage(
                        model=model,
                        usage={
                            "input_tokens": resp.usage.prompt_tokens,
                            "output_tokens": resp.usage.completion_tokens,
                            "cache_read_input_tokens": (
                                getattr(
                                    getattr(resp.usage, "prompt_tokens_details", None),
                                    "cached_tokens",
                                    0,
                                )
                                or 0
                            ),
                        },
                    )

                # No tool calls → conversation is done.
                if not tool_calls:
                    yield LLMEvent.done()
                    return

                # Surface every tool_call so the runner can mirror them
                # to the UI before we dispatch.
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {"_raw": tc.function.arguments}
                    yield LLMEvent.tool_call(
                        tool_name=tc.function.name,
                        tool_call_id=tc.id,
                        arguments=args,
                    )

                # If the runner didn't supply a dispatcher, stop here —
                # caller will pump the loop themselves.
                if tool_dispatch is None:
                    yield LLMEvent.done()
                    return

                # Execute each tool call sequentially, append results to
                # messages so the next API call sees them, and yield
                # tool_result events for the UI.
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        result = await tool_dispatch(tc.id, tc.function.name, args)
                    except Exception as exc:
                        logger.exception(
                            "tool_dispatch failed for %s", tc.function.name
                        )
                        result = f"[tool_dispatch error] {exc!r}"
                        yield LLMEvent.tool_result(
                            tool_call_id=tc.id, content=result, is_error=True
                        )
                    else:
                        yield LLMEvent.tool_result(
                            tool_call_id=tc.id, content=result
                        )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result if isinstance(result, str) else str(result),
                        }
                    )

                # Loop again — the model now sees the tool results and
                # decides what to do next.
            else:
                # max_turns exhausted without a stop. Tell the runner.
                yield LLMEvent.error(
                    f"OpenAI agent loop hit max_turns={max_turns} without finishing"
                )

        except Exception as e:
            logger.exception("OpenAIProvider.run failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()
