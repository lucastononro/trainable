"""Gemini provider — google-genai SDK with multi-turn function calling.

Mirrors openai_provider.py: drives a full agent loop internally when a
``tool_dispatch`` callback is supplied; without one, stops at the first
tool boundary.

Imports google.genai lazily.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Awaitable, Callable

from .base import LLMEvent, LLMProvider, ProviderCapabilities
from .thinking import to_provider_config

logger = logging.getLogger(__name__)


ToolDispatch = Callable[[str, str, dict], Awaitable[str]]


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
        # Gemini has explicit context caching; we don't expose a knob today.
        supports_prompt_cache=True,
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
        mcp_servers: dict | None = None,  # noqa: ARG002
        max_turns: int = 30,
        timeout_seconds: int = 1800,  # noqa: ARG002
        tool_dispatch: ToolDispatch | None = None,
        thinking_level: str | None = None,
        **kwargs: Any,  # noqa: ARG002
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

            # Resolve thinking config once. to_provider_config returns
            # {"thinking_config": {...}} for Gemini — the genai SDK takes
            # it on the GenerateContentConfig.
            thinking_extra = to_provider_config("gemini", thinking_level, model_id=model)
            thinking_cfg = (
                genai_types.ThinkingConfig(**thinking_extra["thinking_config"])
                if thinking_extra.get("thinking_config")
                else None
            )

            cfg = genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=(
                    [genai_types.Tool(function_declarations=fn_decls)]
                    if fn_decls
                    else None
                ),
                thinking_config=thinking_cfg,
            )

            # Conversation history. Each turn appends a user/model entry
            # plus optional function_response parts.
            contents: list = [
                genai_types.Content(
                    role="user", parts=[genai_types.Part.from_text(text=prompt)]
                )
            ]

            for turn in range(max_turns):
                resp = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=cfg,
                )

                # Surface usage for cost tracking.
                usage_md = getattr(resp, "usage_metadata", None)
                if usage_md:
                    yield LLMEvent.usage(
                        model=model,
                        usage={
                            "input_tokens": getattr(
                                usage_md, "prompt_token_count", 0
                            )
                            or 0,
                            "output_tokens": getattr(
                                usage_md, "candidates_token_count", 0
                            )
                            or 0,
                            "cache_read_input_tokens": getattr(
                                usage_md, "cached_content_token_count", 0
                            )
                            or 0,
                        },
                    )

                cand = (resp.candidates or [None])[0]
                if cand is None or cand.content is None:
                    yield LLMEvent.done()
                    return

                # Mirror Gemini's reply into our contents history so the
                # next turn's function_response parts have a partner.
                contents.append(cand.content)

                # Walk parts: yield text + tool_call events, collect
                # function_calls for dispatch.
                fn_calls: list = []
                for part in cand.content.parts or []:
                    text = getattr(part, "text", None)
                    if text:
                        yield LLMEvent.text(text)
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
                        # Gemini doesn't give us a stable call_id; use
                        # the function name + index in this turn.
                        call_id = f"{fn_call.name}#{turn}#{len(fn_calls)}"
                        yield LLMEvent.tool_call(
                            tool_name=fn_call.name,
                            tool_call_id=call_id,
                            arguments=args,
                        )
                        fn_calls.append((call_id, fn_call.name, args))

                # No tool calls → done.
                if not fn_calls:
                    yield LLMEvent.done()
                    return

                if tool_dispatch is None:
                    yield LLMEvent.done()
                    return

                # Execute and feed back as a single user-turn payload of
                # function_response parts (Gemini's expected format).
                response_parts = []
                for call_id, name, args in fn_calls:
                    try:
                        result = await tool_dispatch(call_id, name, args)
                    except Exception as exc:
                        logger.exception("tool_dispatch failed for %s", name)
                        result = f"[tool_dispatch error] {exc!r}"
                        yield LLMEvent.tool_result(
                            tool_call_id=call_id, content=result, is_error=True
                        )
                    else:
                        yield LLMEvent.tool_result(
                            tool_call_id=call_id, content=result
                        )
                    response_parts.append(
                        genai_types.Part.from_function_response(
                            name=name,
                            response={
                                "result": result
                                if isinstance(result, (str, int, float, bool))
                                else str(result)
                            },
                        )
                    )

                contents.append(
                    genai_types.Content(role="user", parts=response_parts)
                )

            else:
                yield LLMEvent.error(
                    f"Gemini agent loop hit max_turns={max_turns} without finishing"
                )

        except Exception as e:
            logger.exception("GeminiProvider.run failed")
            yield LLMEvent.error(str(e))

        yield LLMEvent.done()
