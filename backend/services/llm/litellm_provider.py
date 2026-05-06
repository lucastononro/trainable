"""LiteLLM provider — uniform OpenAI-shaped tool calling for many backends.

Acts as a catch-all for backends without dedicated providers (Groq, Mistral,
DeepSeek, Together, OpenRouter, Bedrock, etc.). The agent YAML's `model:`
field doubles as the backend selector — e.g. `groq/llama-3.3-70b`,
`mistral/mistral-large`, `together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo`.

Auth is per-backend: each LiteLLM backend reads its own env var
(GROQ_API_KEY, MISTRAL_API_KEY, etc.). The auth resolver only checks that
*at least one* such key exists; missing-key errors for a specific backend
surface at call time with a clear message.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from .auth import resolve_credentials
from .auth._base import ProviderUnavailable
from .base import LLMEvent, LLMProvider, ProviderCapabilities

logger = logging.getLogger(__name__)


def _to_openai_tool(name: str, description: str, input_schema: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema or {"type": "object", "properties": {}},
        },
    }


def _import_litellm():
    try:
        import litellm  # type: ignore
    except ImportError as e:
        raise ProviderUnavailable(
            "litellm not installed — `pip install litellm`"
        ) from e
    return litellm


class LiteLLMProvider(LLMProvider):
    capabilities = ProviderCapabilities(
        name="litellm",
        supports_subagents=False,
        supports_mcp=False,
        supports_prompt_cache=False,
        supports_streaming=True,
        default_model="groq/llama-3.3-70b-versatile",
    )

    def __init__(self):
        self.creds = resolve_credentials("litellm")
        self._litellm = None

    @property
    def auth_mode(self) -> str:
        return self.creds.mode

    def _module(self):
        if self._litellm is None:
            self._litellm = _import_litellm()
        return self._litellm

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
        litellm = self._module()

        oai_tools = [
            _to_openai_tool(
                t["name"], t.get("description", ""), t.get("input_schema") or {}
            )
            for t in (tools or [])
        ]
        messages = kwargs.get("messages") or [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            resp = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=oai_tools or None,
                stream=False,
                timeout=timeout_seconds,
            )

            choice = resp.choices[0]
            msg = choice.message
            content = getattr(msg, "content", None)
            if content:
                yield LLMEvent.text(content)

            for tc in getattr(msg, "tool_calls", None) or []:
                fn = getattr(tc, "function", None) or {}
                fn_name = (
                    getattr(fn, "name", None)
                    or (fn.get("name") if isinstance(fn, dict) else None)
                    or ""
                )
                raw_args = (
                    getattr(fn, "arguments", None)
                    or (fn.get("arguments") if isinstance(fn, dict) else None)
                    or "{}"
                )
                try:
                    args = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str)
                        else dict(raw_args)
                    )
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
                yield LLMEvent.tool_call(
                    tool_name=fn_name,
                    tool_call_id=getattr(tc, "id", None) or fn_name,
                    arguments=args,
                )

            usage = getattr(resp, "usage", None)
            if usage:
                yield LLMEvent.usage(
                    model=model,
                    usage={
                        "input_tokens": getattr(usage, "prompt_tokens", 0)
                        or (
                            usage.get("prompt_tokens", 0)
                            if isinstance(usage, dict)
                            else 0
                        ),
                        "output_tokens": getattr(usage, "completion_tokens", 0)
                        or (
                            usage.get("completion_tokens", 0)
                            if isinstance(usage, dict)
                            else 0
                        ),
                    },
                    total_cost_usd=getattr(resp, "_response_cost", None),
                )
        except Exception as e:
            logger.exception("LiteLLMProvider.run failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()
