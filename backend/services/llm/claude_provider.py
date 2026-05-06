"""Claude provider — two transports behind one LLMProvider interface.

Auth resolution decides at construction time which path to use:

- ``oauth_cli`` (CLAUDE_CODE_OAUTH_TOKEN set) → claude-agent-sdk's
  ``query()`` subprocess. This is the rich path: real-time streaming
  events, MCP-server tool dispatch, sub-agent recursion, file
  checkpointing, all delegated to the Claude Code CLI. Free against the
  user's Claude.ai subscription quota during local dev.

- ``api_key`` (ANTHROPIC_API_KEY set, no OAuth) → ``anthropic`` Python
  SDK directly with a multi-turn agent loop driven by the runner's
  ``tool_dispatch`` callback. Production-shaped: per-token billing, no
  CLI subprocess, narrower feature surface (no MCP, no sub-agents).

Both transports yield the same normalized ``LLMEvent`` stream so the
runner doesn't have to care which one is active.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Awaitable, Callable

from .auth import resolve as resolve_auth
from .base import LLMEvent, LLMProvider, ProviderCapabilities
from .thinking import to_provider_config

logger = logging.getLogger(__name__)


ToolDispatch = Callable[[str, str, dict], Awaitable[str]]


def _make_anthropic_client():
    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed — `pip install anthropic`"
        ) from e
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY env var not set")
    return AsyncAnthropic(api_key=api_key)


def _to_anthropic_tool(name: str, description: str, input_schema: dict) -> dict:
    """Anthropic's tools shape — close to JSON Schema with `name`/`description`/`input_schema` at the top level."""
    return {
        "name": name,
        "description": description,
        "input_schema": input_schema or {"type": "object", "properties": {}},
    }


class ClaudeProvider(LLMProvider):
    capabilities = ProviderCapabilities(
        name="claude",
        supports_subagents=True,
        supports_mcp=True,
        supports_prompt_cache=True,
        supports_streaming=True,
        default_model="claude-sonnet-4-6",
    )

    def __init__(self):
        # Resolve auth eagerly so capabilities can adjust.
        self.creds = resolve_auth("claude")
        # When we're API-key bound, MCP and sub-agents aren't available
        # via the direct SDK — flip the flags so callers (the runner,
        # the picker UI) can plan accordingly.
        if self.creds.mode != "oauth_cli":
            self.capabilities = ProviderCapabilities(
                name="claude",
                supports_subagents=False,
                supports_mcp=False,
                supports_prompt_cache=True,
                supports_streaming=True,
                default_model="claude-sonnet-4-6",
            )
        self._anthropic_client = None

    # ----- OAuth (claude-agent-sdk) transport -----------------------------

    async def _run_oauth(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None,
        mcp_servers: dict | None,
        max_turns: int,
        thinking_level: str | None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMEvent]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            UserMessage,
            query,
        )

        tool_names = [t["name"] if isinstance(t, dict) else t for t in (tools or [])]
        thinking_kwargs = to_provider_config("claude", thinking_level, model_id=model)

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            permission_mode=kwargs.get("permission_mode", "bypassPermissions"),
            max_turns=max_turns,
            tools=tool_names,
            allowed_tools=tool_names,
            mcp_servers=mcp_servers or {},
            env=kwargs.get("env", {}),
            **thinking_kwargs,
        )

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        text = getattr(block, "text", None)
                        if text:
                            yield LLMEvent.text(text)
                            continue
                        tool_name = getattr(block, "name", None)
                        tool_input = getattr(block, "input", None)
                        tool_use_id = getattr(block, "id", None)
                        if tool_name and tool_input is not None:
                            yield LLMEvent.tool_call(
                                tool_name=tool_name,
                                tool_call_id=tool_use_id or "",
                                arguments=tool_input
                                if isinstance(tool_input, dict)
                                else {"raw": tool_input},
                            )
                elif isinstance(message, UserMessage):
                    for block in getattr(message, "content", []) or []:
                        tool_use_id = getattr(block, "tool_use_id", None)
                        if not tool_use_id:
                            continue
                        yield LLMEvent.tool_result(
                            tool_call_id=tool_use_id,
                            content=getattr(block, "content", None),
                            is_error=bool(getattr(block, "is_error", False)),
                        )
                elif isinstance(message, ResultMessage):
                    if message.model_usage:
                        for m_name, m_usage in message.model_usage.items():
                            yield LLMEvent.usage(
                                model=m_name,
                                usage=m_usage,
                                total_cost_usd=message.total_cost_usd,
                            )
                    elif message.usage:
                        yield LLMEvent.usage(
                            model=model,
                            usage=message.usage,
                            total_cost_usd=message.total_cost_usd,
                        )
        except Exception as e:
            logger.exception("ClaudeProvider OAuth path failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()

    # ----- API-key (anthropic SDK) transport -------------------------------

    def _client_or_raise(self):
        if self._anthropic_client is None:
            self._anthropic_client = _make_anthropic_client()
        return self._anthropic_client

    async def _run_api_key(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None,
        max_turns: int,
        tool_dispatch: ToolDispatch | None,
        thinking_level: str | None,
    ) -> AsyncIterator[LLMEvent]:
        client = self._client_or_raise()

        anthr_tools = [
            _to_anthropic_tool(
                t["name"], t.get("description", ""), t.get("input_schema") or {}
            )
            for t in (tools or [])
        ]

        thinking_kwargs = to_provider_config("claude", thinking_level, model_id=model)

        messages: list[dict] = [{"role": "user", "content": prompt}]

        try:
            for turn in range(max_turns):
                resp = await client.messages.create(
                    model=model,
                    system=system_prompt,
                    messages=messages,
                    tools=anthr_tools or [],
                    max_tokens=4096,
                    **thinking_kwargs,
                )

                # Usage event
                if resp.usage:
                    yield LLMEvent.usage(
                        model=model,
                        usage={
                            "input_tokens": resp.usage.input_tokens,
                            "output_tokens": resp.usage.output_tokens,
                            "cache_read_input_tokens": getattr(
                                resp.usage, "cache_read_input_tokens", 0
                            )
                            or 0,
                            "cache_creation_input_tokens": getattr(
                                resp.usage, "cache_creation_input_tokens", 0
                            )
                            or 0,
                        },
                    )

                # Walk content blocks: yield text + tool_call events,
                # collect tool_use for dispatch.
                tool_uses = []
                assistant_blocks = []
                for block in resp.content:
                    btype = getattr(block, "type", None)
                    if btype == "text":
                        text = getattr(block, "text", "")
                        if text:
                            yield LLMEvent.text(text)
                            assistant_blocks.append({"type": "text", "text": text})
                    elif btype == "tool_use":
                        tu_id = getattr(block, "id", "")
                        tu_name = getattr(block, "name", "")
                        tu_input = getattr(block, "input", {}) or {}
                        yield LLMEvent.tool_call(
                            tool_name=tu_name,
                            tool_call_id=tu_id,
                            arguments=tu_input
                            if isinstance(tu_input, dict)
                            else {"raw": tu_input},
                        )
                        tool_uses.append((tu_id, tu_name, tu_input))
                        assistant_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tu_id,
                                "name": tu_name,
                                "input": tu_input,
                            }
                        )
                    elif btype == "thinking":
                        # Pass thinking blocks through silently — they
                        # need to be in messages history for follow-ups.
                        assistant_blocks.append(
                            {
                                "type": "thinking",
                                "thinking": getattr(block, "thinking", ""),
                                "signature": getattr(block, "signature", ""),
                            }
                        )

                # Append assistant turn to history.
                messages.append({"role": "assistant", "content": assistant_blocks})

                stop_reason = getattr(resp, "stop_reason", None)
                if stop_reason != "tool_use" or not tool_uses:
                    yield LLMEvent.done()
                    return

                if tool_dispatch is None:
                    yield LLMEvent.done()
                    return

                # Dispatch tool uses, build a single user-turn payload
                # of tool_result blocks, append, and loop.
                tool_results = []
                for tu_id, tu_name, tu_input in tool_uses:
                    args = (
                        tu_input
                        if isinstance(tu_input, dict)
                        else {"raw": tu_input}
                    )
                    try:
                        result = await tool_dispatch(tu_id, tu_name, args)
                        is_error = False
                    except Exception as exc:
                        logger.exception("tool_dispatch failed for %s", tu_name)
                        result = f"[tool_dispatch error] {exc!r}"
                        is_error = True

                    yield LLMEvent.tool_result(
                        tool_call_id=tu_id, content=result, is_error=is_error
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu_id,
                            "content": result if isinstance(result, str) else json.dumps(result),
                            **({"is_error": True} if is_error else {}),
                        }
                    )

                messages.append({"role": "user", "content": tool_results})

            else:
                yield LLMEvent.error(
                    f"Claude agent loop hit max_turns={max_turns} without finishing"
                )

        except Exception as e:
            logger.exception("ClaudeProvider API-key path failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()

    # ----- public entry point ---------------------------------------------

    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None = None,
        mcp_servers: dict | None = None,
        max_turns: int = 30,
        timeout_seconds: int = 1800,  # noqa: ARG002 — runner owns the outer timeout
        tool_dispatch: ToolDispatch | None = None,
        thinking_level: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMEvent]:
        if self.creds.mode == "oauth_cli":
            async for ev in self._run_oauth(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                tools=tools,
                mcp_servers=mcp_servers,
                max_turns=max_turns,
                thinking_level=thinking_level,
                **kwargs,
            ):
                yield ev
            return

        if self.creds.mode == "api_key":
            async for ev in self._run_api_key(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                tools=tools,
                max_turns=max_turns,
                tool_dispatch=tool_dispatch,
                thinking_level=thinking_level,
            ):
                yield ev
            return

        yield LLMEvent.error(
            f"Claude provider has no credentials. "
            f"Set one of: {', '.join(self.creds.missing_env)}"
        )
        yield LLMEvent.done()
