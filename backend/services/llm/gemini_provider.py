"""Gemini provider — google-genai SDK with function calling.

Mirrors openai_provider.py: single-pass tool boundary, no sub-agent delegation,
no MCP. Imports google.genai lazily.
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
        from google import genai  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "google-genai SDK not installed — `pip install google-genai`"
        ) from e
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) not set")
    return genai.Client(api_key=api_key)


class GeminiProvider(LLMProvider):
    capabilities = ProviderCapabilities(
        name="gemini",
        supports_subagents=False,
        supports_mcp=False,
        supports_prompt_cache=True,  # Gemini has explicit context caching
        supports_streaming=True,
        default_model="gemini-2.5-pro",
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
        try:
            client = self._client_or_raise()

            # Translate tools to Gemini's function-declaration shape.
            fn_decls = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema")
                    or {"type": "object", "properties": {}},
                }
                for t in (tools or [])
            ]
            from google.genai import types as genai_types  # type: ignore

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
            logger.exception("GeminiProvider.run failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()
