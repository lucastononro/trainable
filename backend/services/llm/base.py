"""LLMProvider protocol — the contract every provider implementation honors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable


EventKind = Literal[
    "text",  # streamed assistant text
    "tool_call",  # assistant emitted a tool_use block
    "tool_result",  # tool execution returned a result
    "usage",  # token + cost counters at end of turn
    "error",  # provider error
    "done",  # final marker — provider has nothing more to emit
]


@dataclass
class LLMEvent:
    """Normalized event emitted by any provider."""

    kind: EventKind
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def text(cls, text: str) -> "LLMEvent":
        return cls("text", {"text": text})

    @classmethod
    def tool_call(
        cls,
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: dict,
        provider_metadata: dict | None = None,
    ) -> "LLMEvent":
        """Construct a tool_call event.

        `provider_metadata` is an opaque per-call bag the runner stores on
        the assistant message and passes back to the provider on the next
        turn. It's how providers thread their own continuation tokens
        through the runner without leaking them into the abstraction.
        Gemini 3 uses this for `thought_signature` — without it, multi-turn
        function calls are rejected with INVALID_ARGUMENT.
        """
        data: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
        }
        if provider_metadata:
            data["provider_metadata"] = provider_metadata
        return cls("tool_call", data)

    @classmethod
    def tool_result(
        cls, *, tool_call_id: str, content: Any, is_error: bool = False
    ) -> "LLMEvent":
        return cls(
            "tool_result",
            {
                "tool_call_id": tool_call_id,
                "content": content,
                "is_error": is_error,
            },
        )

    @classmethod
    def usage(
        cls, *, model: str, usage: dict, total_cost_usd: float | None = None
    ) -> "LLMEvent":
        return cls(
            "usage",
            {
                "model": model,
                "usage": usage,
                "total_cost_usd": total_cost_usd,
            },
        )

    @classmethod
    def error(cls, message: str) -> "LLMEvent":
        return cls("error", {"message": message})

    @classmethod
    def done(cls) -> "LLMEvent":
        return cls("done", {})


@dataclass
class ProviderCapabilities:
    """What a provider can do. Used by the runner to gate features."""

    name: str
    supports_mcp: bool = False
    supports_prompt_cache: bool = False
    supports_streaming: bool = True
    default_model: str = ""


@runtime_checkable
class LLMProvider(Protocol):
    """Single interface every provider implements.

    The Claude provider implements this on top of claude-agent-sdk's
    `query()`. Lighter providers (OpenAI/Gemini) implement it on top of
    their native SDKs' streaming chat-completion API.
    """

    capabilities: ProviderCapabilities

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
        **provider_specific_kwargs: Any,
    ) -> AsyncIterator[LLMEvent]:
        """Run a chat session and yield normalized LLMEvents.

        Implementations may receive `provider_specific_kwargs` for things
        the abstraction can't represent yet (e.g. Claude's
        ClaudeAgentOptions.permission_mode). Callers should pass them
        through unchanged.
        """
        ...
