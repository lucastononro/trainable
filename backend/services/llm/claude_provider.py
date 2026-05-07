"""Claude provider — wraps `claude-agent-sdk` query() into an LLMProvider.

The Claude path stays special-cased (sub-agent SDK calls, MCP, ephemeral
cache_control, clarifications) — this provider exists primarily so
non-Claude callers can opt out via the factory while still routing through
the same surface.

OAuth-aware usage tracking
==========================

claude-agent-sdk's `ResultMessage.usage` / `.model_usage` are routinely
empty for Claude Code OAuth (subscription) users — Anthropic doesn't
bill subscription users per call so the SDK skips the aggregate. But
every `AssistantMessage` still carries a `usage` dict with the real
token counts.

We accumulate AssistantMessage.usage per model and:
  - Yield each AssistantMessage delta as a `usage` event with
    `partial=True` so the runner can broadcast live SSE cost updates
    without writing a DB row per turn.
  - On ResultMessage, prefer the SDK aggregate; fall back to the
    accumulator when the aggregate is empty (the OAuth case). The
    final event has `partial=False` so the runner records exactly
    one DB row per run, no double-counting.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    UserMessage,
    query,
)

from .base import LLMEvent, LLMProvider, ProviderCapabilities

logger = logging.getLogger(__name__)


# camelCase → snake_case key map covering Anthropic + claude-agent-sdk
# variants. ResultMessage.model_usage uses camelCase (inputTokens,
# cacheReadInputTokens); AssistantMessage.usage uses Anthropic-API
# snake_case (input_tokens, cache_read_input_tokens). Normalize so the
# runner's record_llm_usage sees a single shape.
_USAGE_KEY_ALIASES: dict[str, str] = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
    "promptTokens": "input_tokens",
    "completionTokens": "output_tokens",
    "prompt_tokens": "input_tokens",
    "completion_tokens": "output_tokens",
    "costUSD": "cost_usd",
}


def _normalize_usage(u: dict | None) -> dict:
    """Coerce a usage dict into snake_case Anthropic-shaped keys."""
    if not isinstance(u, dict):
        return {}
    out: dict = {}
    for k, v in u.items():
        target = _USAGE_KEY_ALIASES.get(k, k)
        try:
            if target == "cost_usd":
                out[target] = float(v or 0.0)
            else:
                out[target] = int(v or 0)
        except (TypeError, ValueError):
            continue
    return out


def _is_empty(usage: dict | None) -> bool:
    """True if the usage dict has no useful token counts."""
    if not usage:
        return True
    for k in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        if int(usage.get(k, 0) or 0) > 0:
            return False
    return True


class ClaudeProvider(LLMProvider):
    capabilities = ProviderCapabilities(
        name="claude",
        supports_subagents=True,
        supports_mcp=True,
        supports_prompt_cache=True,
        supports_streaming=True,
        default_model="claude-sonnet-4-6",
    )

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
        tool_names = [t["name"] if isinstance(t, dict) else t for t in (tools or [])]

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            permission_mode=kwargs.get("permission_mode", "bypassPermissions"),
            max_turns=max_turns,
            tools=tool_names,
            allowed_tools=tool_names,
            mcp_servers=mcp_servers or {},
            env=kwargs.get("env", {}),
        )

        # Per-turn usage accumulator keyed by model name. Required so
        # OAuth users (whose ResultMessage usage is often empty) still
        # get accurate token counts attributed to the session.
        accumulated: dict[str, dict] = {}

        def _bump(model_name: str, turn_usage: dict | None) -> None:
            norm = _normalize_usage(turn_usage)
            if _is_empty(norm):
                return
            bucket = accumulated.setdefault(model_name, {})
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                bucket[k] = int(bucket.get(k, 0) or 0) + int(norm.get(k, 0) or 0)

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

                    # Emit a partial usage event per AssistantMessage so the
                    # runner can broadcast live cost feedback. The data is
                    # also accumulated as a fallback for ResultMessage.
                    turn_model = getattr(message, "model", None) or model
                    turn_usage = getattr(message, "usage", None)
                    norm = _normalize_usage(turn_usage)
                    if not _is_empty(norm):
                        _bump(turn_model, turn_usage)
                        evt = LLMEvent.usage(
                            model=turn_model,
                            usage=norm,
                            total_cost_usd=None,
                        )
                        evt.data["partial"] = True
                        yield evt

                elif isinstance(message, UserMessage):
                    # Tool results come framed as a UserMessage with
                    # ToolResultBlock content. We surface them so non-Claude
                    # consumers of this provider can mirror the events.
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
                    # Resolution order, in priority:
                    #   1. ResultMessage.model_usage (per-model aggregate)
                    #   2. ResultMessage.usage (single-model aggregate)
                    #   3. accumulated[*] (per-turn fallback — the OAuth path
                    #      leaves 1 and 2 empty)
                    sdk_used = False
                    if message.model_usage:
                        for m_name, m_usage in message.model_usage.items():
                            norm = _normalize_usage(m_usage)
                            if _is_empty(norm):
                                continue
                            sdk_used = True
                            yield LLMEvent.usage(
                                model=m_name,
                                usage=norm,
                                total_cost_usd=message.total_cost_usd,
                            )
                    if not sdk_used and message.usage:
                        norm = _normalize_usage(message.usage)
                        if not _is_empty(norm):
                            sdk_used = True
                            yield LLMEvent.usage(
                                model=model,
                                usage=norm,
                                total_cost_usd=message.total_cost_usd,
                            )
                    if not sdk_used and accumulated:
                        logger.info(
                            "Claude OAuth path: ResultMessage usage was empty, "
                            "falling back to accumulated per-turn usage across "
                            "%d model(s)",
                            len(accumulated),
                        )
                        for m_name, m_usage in accumulated.items():
                            yield LLMEvent.usage(
                                model=m_name,
                                usage=m_usage,
                                total_cost_usd=message.total_cost_usd,
                            )

        except Exception as e:
            logger.exception("ClaudeProvider.run failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()
