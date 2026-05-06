"""Gemini provider — google-genai SDK with function calling.

Two transports:
  - api_key: google-genai SDK with GEMINI_API_KEY / GOOGLE_API_KEY.
  - oauth_cli: Gemini CLI subprocess via OAuth (~/.gemini/oauth_creds.json).
    Text-only in this transport.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from .auth import resolve_credentials
from .auth._base import Credentials, ProviderUnavailable
from .base import LLMEvent, LLMProvider, ProviderCapabilities
from .transport import gemini_cli

logger = logging.getLogger(__name__)


def _make_sdk_client(creds: Credentials):
    try:
        from google import genai  # type: ignore
    except ImportError as e:
        raise ProviderUnavailable(
            "google-genai SDK not installed — `pip install google-genai`"
        ) from e
    return genai.Client(api_key=creds.token)


class GeminiProvider(LLMProvider):
    capabilities = ProviderCapabilities(
        name="gemini",
        supports_subagents=False,
        supports_mcp=False,
        supports_prompt_cache=True,
        supports_streaming=True,
        default_model="gemini-2.5-pro",
    )

    def __init__(self):
        self.creds: Credentials = resolve_credentials("gemini")
        self._client = None

    @property
    def auth_mode(self) -> str:
        return self.creds.mode

    def _client_or_raise(self):
        if self._client is None:
            self._client = _make_sdk_client(self.creds)
        return self._client

    async def _run_via_gemini_cli(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None,
        timeout_seconds: int,
    ) -> AsyncIterator[LLMEvent]:
        async for event in gemini_cli.stream(
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
    ) -> AsyncIterator[LLMEvent]:
        try:
            client = self._client_or_raise()
            from google.genai import types as genai_types  # type: ignore

            fn_decls = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema")
                    or {"type": "object", "properties": {}},
                }
                for t in (tools or [])
            ]
            cfg = genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[genai_types.Tool(function_declarations=fn_decls)]
                if fn_decls
                else None,
            )

            resp = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=cfg,
            )

            for cand in resp.candidates or []:
                for part in cand.content.parts if cand.content else []:
                    if getattr(part, "text", None):
                        yield LLMEvent.text(part.text)
                    fn_call = getattr(part, "function_call", None)
                    if fn_call:
                        try:
                            args = (
                                json.loads(fn_call.args)
                                if isinstance(fn_call.args, str)
                                else dict(fn_call.args or {})
                            )
                        except json.JSONDecodeError:
                            args = {"_raw": fn_call.args}
                        yield LLMEvent.tool_call(
                            tool_name=fn_call.name,
                            tool_call_id=fn_call.name,
                            arguments=args,
                        )

            usage = getattr(resp, "usage_metadata", None)
            if usage:
                yield LLMEvent.usage(
                    model=model,
                    usage={
                        "input_tokens": getattr(usage, "prompt_token_count", 0),
                        "output_tokens": getattr(usage, "candidates_token_count", 0),
                    },
                )
        except Exception as e:
            logger.exception("GeminiProvider SDK run failed")
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
            async for event in self._run_via_gemini_cli(
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
        ):
            yield event
        yield LLMEvent.done()
