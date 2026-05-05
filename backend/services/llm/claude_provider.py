"""Claude provider — wraps `claude-agent-sdk` query() into an LLMProvider.

The Claude path stays special-cased (sub-agent SDK calls, MCP, ephemeral
cache_control, clarifications) — this provider exists primarily so
non-Claude callers can opt out via the factory while still routing through
the same surface.

The agent runner uses `query()` directly today; this provider exposes the
same flow as a generator that yields normalized LLMEvents. Existing
runner.py callers can migrate at their own pace.
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
            logger.exception("ClaudeProvider.run failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()
