"""OpenAI provider — chat completions with tool calling.

Two transports:
  - api_key: AsyncOpenAI SDK with OPENAI_API_KEY (or OPENAI_BASE_URL for
    OpenAI-compatible deployments). Supports streaming + user-defined tools.
  - oauth_cli: Codex CLI subprocess via OAuth (~/.codex/auth.json). Text-only
    in this transport; the runner sees no tool_call events and the agent must
    work via skill methodology.

The transport is resolved once at construction from the auth/openai resolver.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from .auth import resolve_credentials
from .auth._base import Credentials, ProviderUnavailable
from .base import LLMEvent, LLMProvider, ProviderCapabilities
from .transport import codex_cli

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
        supports_subagents=False,
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

    async def _run_via_codex_cli(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None,
        timeout_seconds: int,
    ) -> AsyncIterator[LLMEvent]:
        async for event in codex_cli.stream(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            tools=tools,
            timeout_seconds=timeout_seconds,
        ):
            yield event

    async def _run_via_sdk(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None,
        max_turns: int,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        client = self._client_or_raise()

        oai_tools = [
            _to_openai_tool(
                t["name"], t.get("description", ""), t.get("input_schema") or {}
            )
            for t in (tools or [])
        ]

        if messages is None:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]

        # Single-pass: yields text + tool_call events. The runner appends
        # tool results and re-invokes for each turn.
        try:
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

            for tc in getattr(msg, "tool_calls", None) or []:
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
        except Exception as e:
            logger.exception("OpenAIProvider SDK run failed")
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
        if self.creds.mode == "oauth_cli":
            async for event in self._run_via_codex_cli(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                tools=tools,
                timeout_seconds=timeout_seconds,
            ):
                yield event
            return

        async for event in self._run_via_sdk(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            tools=tools,
            max_turns=max_turns,
            messages=kwargs.get("messages"),
        ):
            yield event
        yield LLMEvent.done()
