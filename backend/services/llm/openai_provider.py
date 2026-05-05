"""OpenAI provider — chat completions with tool calling.

Single-agent flow only (no sub-agent delegation, no MCP). When the active
agent is configured with `provider: openai`, the runner falls back to a
simpler tool-using loop that translates execute_code-style MCP tools into
OpenAI's function-call shape.

Imports openai lazily so the package's mere presence in the registry
doesn't require the SDK to be installed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

from .base import LLMEvent, LLMProvider, ProviderCapabilities

logger = logging.getLogger(__name__)


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
        supports_prompt_cache=False,  # OpenAI's caching is implicit, not surfaced here
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
        mcp_servers: dict | None = None,
        max_turns: int = 30,
        timeout_seconds: int = 1800,
        **kwargs,
    ) -> AsyncIterator[LLMEvent]:
        client = self._client_or_raise()

        # Tools arrive as the runner's normalized [{name, description, input_schema}]
        # — translate to OpenAI's function-call shape.
        oai_tools = [
            _to_openai_tool(
                t["name"], t.get("description", ""), t.get("input_schema") or {}
            )
            for t in (tools or [])
        ]

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # In-loop tool dispatch is the runner's job; this provider stops at
        # the first tool_call boundary and lets the caller append the
        # tool_result and re-invoke. That keeps the abstraction symmetric
        # with the Claude provider (which dispatches via MCP internally).
        turns = 0
        try:
            while turns < max_turns:
                turns += 1
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=oai_tools or None,
                    stream=False,
                )
                choice = resp.choices[0]
                msg = choice.message

                if msg.content:
                    yield LLMEvent.text(msg.content)

                tool_calls = getattr(msg, "tool_calls", None) or []
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

                if resp.usage:
                    yield LLMEvent.usage(
                        model=model,
                        usage={
                            "input_tokens": resp.usage.prompt_tokens,
                            "output_tokens": resp.usage.completion_tokens,
                        },
                    )

                # OpenAI returns "tool_calls" — the runner must dispatch
                # them and re-enter; in this single-pass impl we stop here.
                # A full agent loop is out of scope until we wire the
                # runner through this provider.
                break

        except Exception as e:
            logger.exception("OpenAIProvider.run failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()
