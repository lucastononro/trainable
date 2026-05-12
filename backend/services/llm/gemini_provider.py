"""Gemini provider — google-genai SDK with function calling.

Auth: google-genai SDK with GEMINI_API_KEY / GOOGLE_API_KEY. No OAuth-CLI
fallback — set the env var or the provider isn't registered.

Conversation shape: the runner emits Chat-Completions-shaped messages
(`role`/`content`/`tool_calls`/`tool_call_id`). We translate them into
Gemini's `Content` list at the boundary so tool round-trips survive across
turns. Without this translation Gemini sees only the original prompt on
every turn, which makes the runner's tool loop nondeterministic and prone
to repeating the same tool call until `agent_max_turns` is exhausted.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from .auth import resolve_credentials
from .auth._base import Credentials, ProviderUnavailable
from .base import LLMEvent, LLMProvider, ProviderCapabilities

logger = logging.getLogger(__name__)


def _make_sdk_client(creds: Credentials):
    try:
        from google import genai  # type: ignore
    except ImportError as e:
        raise ProviderUnavailable(
            "google-genai SDK not installed — `pip install google-genai`"
        ) from e
    return genai.Client(api_key=creds.token)


def _coerce_args(raw: Any) -> dict:
    """Best-effort parse of a tool-call arguments blob (str or dict)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"_raw": raw}
        except json.JSONDecodeError:
            return {"_raw": raw}
    return {}


def _messages_to_gemini_contents(
    messages: list[dict], genai_types
) -> tuple[str | None, list]:
    """Translate Chat-Completions-shaped messages to Gemini Content list.

    Returns (system_instruction, contents). `system_instruction` is the
    concatenated text of any `role=system` messages — Gemini takes it as a
    config field, not as a Content turn. `contents` is the ordered list
    of `Content` objects (alternating user/model with embedded
    function_call / function_response parts).

    Gemini correlates tool results to tool calls **by name**, not by id,
    so the runner's `tool_call_id` is dropped. If two parallel calls hit
    the same tool name in one turn we lose disambiguation — acceptable
    for v1; the runner emits one call per turn for non-Claude paths.
    """
    t = genai_types
    system_parts: list[str] = []
    contents: list = []
    # call_id -> name, so role=tool messages can resolve their target.
    call_id_to_name: dict[str, str] = {}

    for msg in messages or []:
        role = msg.get("role")

        if role == "system":
            txt = msg.get("content")
            if txt:
                system_parts.append(txt)
            continue

        if role == "user":
            txt = msg.get("content") or ""
            contents.append(t.Content(role="user", parts=[t.Part.from_text(text=txt)]))
            continue

        if role == "assistant":
            parts = []
            txt = msg.get("content")
            if txt:
                parts.append(t.Part.from_text(text=txt))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                args = _coerce_args(fn.get("arguments"))
                if tc.get("id"):
                    call_id_to_name[tc["id"]] = name
                # Restore per-call continuation tokens the provider stashed
                # on the response — Gemini 3 rejects history that drops the
                # thought_signature, and the FunctionCall.id wants to
                # round-trip too so the response can be matched.
                pmeta = tc.get("_provider_metadata") or {}
                signature = pmeta.get("thought_signature")
                fc_id = pmeta.get("function_call_id")
                fc_kwargs = {"name": name, "args": args}
                if fc_id:
                    fc_kwargs["id"] = fc_id
                part_kwargs = {"function_call": t.FunctionCall(**fc_kwargs)}
                if signature:
                    part_kwargs["thought_signature"] = signature
                parts.append(t.Part(**part_kwargs))
            if parts:
                contents.append(t.Content(role="model", parts=parts))
            continue

        if role == "tool":
            call_id = msg.get("tool_call_id") or ""
            name = call_id_to_name.get(call_id) or ""
            raw_content = msg.get("content")
            # Gemini wants a JSON-serializable response dict. Stuff strings
            # under `result` so the model has a stable key to read.
            if isinstance(raw_content, dict):
                response = raw_content
            else:
                response = {"result": "" if raw_content is None else str(raw_content)}
            # Echo the FunctionCall.id on the response when we have it —
            # Gemini 3 pairs them by id, not just by name.
            fr_kwargs = {"name": name, "response": response}
            if call_id and call_id != name:
                fr_kwargs["id"] = call_id
            contents.append(
                t.Content(
                    role="user",
                    parts=[t.Part(function_response=t.FunctionResponse(**fr_kwargs))],
                )
            )
            continue

    return ("\n\n".join(p for p in system_parts if p) or None), contents


class GeminiProvider(LLMProvider):
    capabilities = ProviderCapabilities(
        name="gemini",
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

    async def _run_via_sdk(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        tools: list[dict] | None,
        messages: list[dict] | None = None,
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

            # Prefer the structured message history when the runner supplied
            # one; fall back to the bare prompt for one-shot callers.
            if messages:
                sys_from_msgs, contents = _messages_to_gemini_contents(
                    messages, genai_types
                )
                system_instruction = system_prompt or sys_from_msgs
            else:
                system_instruction = system_prompt
                contents = prompt

            cfg = genai_types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=[genai_types.Tool(function_declarations=fn_decls)]
                if fn_decls
                else None,
            )

            resp = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=cfg,
            )

            for cand in resp.candidates or []:
                for part in cand.content.parts if cand.content else []:
                    if getattr(part, "text", None):
                        yield LLMEvent.text(part.text)
                    fn_call = getattr(part, "function_call", None)
                    if fn_call:
                        args = _coerce_args(getattr(fn_call, "args", None))
                        # Prefer the SDK-issued call id; fall back to the
                        # function name so the runner's tool_call_id slot
                        # is never empty.
                        fc_id = getattr(fn_call, "id", None) or fn_call.name
                        # Gemini 3 stamps an opaque thought_signature on
                        # each fn_call Part — we must echo it back on the
                        # next turn or the API rejects the request.
                        signature = getattr(part, "thought_signature", None)
                        pmeta: dict | None = None
                        if signature or getattr(fn_call, "id", None):
                            pmeta = {
                                "thought_signature": signature,
                                "function_call_id": getattr(fn_call, "id", None),
                            }
                        yield LLMEvent.tool_call(
                            tool_name=fn_call.name,
                            tool_call_id=fc_id,
                            arguments=args,
                            provider_metadata=pmeta,
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
        async for event in self._run_via_sdk(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            tools=tools,
            messages=kwargs.get("messages"),
        ):
            yield event
        yield LLMEvent.done()
