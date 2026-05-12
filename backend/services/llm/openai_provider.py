"""OpenAI provider — Responses API with tool calling.

Uses the modern Responses endpoint (`client.responses.create`) rather than
legacy chat.completions. The Responses API is the default surface for
GPT-5+ and reasoning models; it has a slightly different shape:

  * tools are flat (`{type:"function", name, description, parameters}`),
    not nested under a `function:` key
  * conversation state is an `input` list of typed items (`message`,
    `function_call`, `function_call_output`) plus a separate
    `instructions` field for the system prompt
  * usage fields are `input_tokens` / `output_tokens` (matching what the
    runner already records — no rename needed)

Auth: AsyncOpenAI SDK with OPENAI_API_KEY (or OPENAI_BASE_URL for
OpenAI-compatible deployments). The runner passes Chat-Completions-shaped
messages (`role`/`content`/`tool_calls`/`tool_call_id`); we translate them
to Responses items at the boundary so the runner stays provider-neutral.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from .auth import resolve_credentials
from .auth._base import Credentials, ProviderUnavailable
from .base import LLMEvent, LLMProvider, ProviderCapabilities

logger = logging.getLogger(__name__)


def _to_responses_tool(name: str, description: str, input_schema: dict) -> dict:
    """Responses API tool shape — flat, no `function:` nesting."""
    return {
        "type": "function",
        "name": name,
        "description": description or "",
        "parameters": input_schema or {"type": "object", "properties": {}},
        # `strict` is required by the type but optional in practice; leave
        # off so callers' loose JSON-Schema fragments don't get rejected.
    }


def _messages_to_responses_input(
    messages: list[dict],
) -> tuple[str | None, list[dict]]:
    """Translate Chat-Completions-shaped messages to Responses input.

    Returns (instructions, input_items).

    The runner emits messages in Chat Completions shape:
      - {"role": "system", "content": str}
      - {"role": "user", "content": str}
      - {"role": "assistant", "content": str|None, "tool_calls": [
            {"id": ..., "type": "function",
             "function": {"name": ..., "arguments": json_str}}]}
      - {"role": "tool", "tool_call_id": id, "content": str}

    Responses wants:
      - system text  -> `instructions=` string (concatenated if multiple)
      - user/assistant text -> `{role, content}` items
      - assistant tool calls -> `{type:"function_call", call_id, name, arguments}`
      - tool results -> `{type:"function_call_output", call_id, output}`
    """
    instructions_parts: list[str] = []
    items: list[dict] = []
    for msg in messages or []:
        role = msg.get("role")
        if role == "system":
            text = msg.get("content")
            if text:
                instructions_parts.append(text)
            continue
        if role == "user":
            items.append({"role": "user", "content": msg.get("content") or ""})
            continue
        if role == "assistant":
            content = msg.get("content")
            if content:
                items.append({"role": "assistant", "content": content})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id") or "",
                        "name": fn.get("name") or "",
                        "arguments": fn.get("arguments") or "{}",
                    }
                )
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id") or "",
                    "output": msg.get("content") or "",
                }
            )
            continue
    instructions = "\n\n".join(p for p in instructions_parts if p) or None
    return instructions, items


def _make_sdk_client(creds: Credentials):
    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        raise ProviderUnavailable(
            "openai SDK not installed — `pip install openai`"
        ) from e
    kwargs: dict = {"api_key": creds.token}
    base_url = (creds.extra or {}).get("base_url")
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


class OpenAIProvider(LLMProvider):
    capabilities = ProviderCapabilities(
        name="openai",
        supports_mcp=False,
        supports_prompt_cache=False,
        supports_streaming=True,
        default_model="gpt-5",
    )

    def __init__(self):
        # `resolve_credentials` raises ProviderUnavailable if neither
        # OPENAI_API_KEY nor a usable Codex CLI is present. The factory
        # catches that and skips registering us.
        self.creds: Credentials = resolve_credentials("openai")
        self._client = None

    @property
    def auth_mode(self) -> str:
        return self.creds.mode

    def _client_or_raise(self):
        if self._client is None:
            self._client = _make_sdk_client(self.creds)
        return self._client

    async def _run_via_sdk(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None,
        max_turns: int,
        messages: list[dict] | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        client = self._client_or_raise()

        oai_tools = [
            _to_responses_tool(
                t["name"], t.get("description", ""), t.get("input_schema") or {}
            )
            for t in (tools or [])
        ]

        if messages is None:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]

        instructions, input_items = _messages_to_responses_input(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "instructions": instructions,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools
        if reasoning_effort:
            # Responses accepts {"effort": "minimal|low|medium|high"}.
            # Silently ignored by non-reasoning models.
            kwargs["reasoning"] = {"effort": reasoning_effort}

        try:
            resp = await client.responses.create(**kwargs)

            # Iterate the typed output items. Each item is one of:
            #   message     -> assistant text (one or more output_text blocks)
            #   function_call -> tool call to dispatch
            #   reasoning   -> reasoning summary (ignored for now)
            for item in resp.output or []:
                item_type = getattr(item, "type", None)

                if item_type == "message":
                    parts: list[str] = []
                    for block in getattr(item, "content", None) or []:
                        block_type = getattr(block, "type", None)
                        if block_type == "output_text":
                            parts.append(getattr(block, "text", "") or "")
                    text = "".join(parts)
                    if text:
                        yield LLMEvent.text(text)

                elif item_type == "function_call":
                    raw_args = getattr(item, "arguments", "") or "{}"
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {"_raw": raw_args}
                    yield LLMEvent.tool_call(
                        tool_name=getattr(item, "name", "") or "",
                        tool_call_id=getattr(item, "call_id", "") or "",
                        arguments=args,
                    )

                # other item_types (reasoning, web_search, etc.) are ignored

            usage = getattr(resp, "usage", None)
            if usage is not None:
                yield LLMEvent.usage(
                    model=model,
                    usage={
                        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                    },
                )
        except Exception as e:
            logger.exception("OpenAIProvider Responses call failed")
            yield LLMEvent.error(str(e))

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
        async for event in self._run_via_sdk(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            tools=tools,
            max_turns=max_turns,
            messages=kwargs.get("messages"),
            reasoning_effort=kwargs.get("reasoning_effort"),
        ):
            yield event
        yield LLMEvent.done()
