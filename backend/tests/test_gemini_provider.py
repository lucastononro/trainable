"""Unit tests for the Gemini provider's message translation.

These tests do NOT call the real Gemini API — the SDK client is mocked.
They assert that a Chat-Completions-shaped `messages` list is translated
into Gemini's `Content` list with `function_call` / `function_response`
Parts in the right order, so multi-turn tool round-trips actually work.

E2E coverage lives in `test_providers_e2e.py::test_gemini_tool_call_loop`
behind `RUN_LLM_E2E=1`.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def gemini_types():
    """The real google.genai types — imported lazily so the test file
    still collects on machines that don't have the SDK installed."""
    pytest.importorskip("google.genai")
    from google.genai import types

    return types


class TestMessageTranslation:
    """Direct unit tests on `_messages_to_gemini_contents` — no SDK call."""

    def test_system_messages_concatenate_into_instruction(self, gemini_types):
        from services.llm.gemini_provider import _messages_to_gemini_contents

        msgs = [
            {"role": "system", "content": "be terse"},
            {"role": "system", "content": "answer in english"},
            {"role": "user", "content": "hi"},
        ]
        sys_inst, contents = _messages_to_gemini_contents(msgs, gemini_types)
        assert sys_inst == "be terse\n\nanswer in english"
        assert len(contents) == 1
        assert contents[0].role == "user"

    def test_user_and_assistant_text_become_alternating_contents(self, gemini_types):
        from services.llm.gemini_provider import _messages_to_gemini_contents

        msgs = [
            {"role": "user", "content": "ping"},
            {"role": "assistant", "content": "pong"},
            {"role": "user", "content": "pong again?"},
        ]
        sys_inst, contents = _messages_to_gemini_contents(msgs, gemini_types)
        assert sys_inst is None
        roles = [c.role for c in contents]
        assert roles == ["user", "model", "user"]
        # First user message keeps its text intact
        assert contents[0].parts[0].text == "ping"
        assert contents[1].parts[0].text == "pong"

    def test_assistant_tool_call_becomes_function_call_part(self, gemini_types):
        """A regular Chat-Completions assistant tool_call message must
        round-trip into a `Part(function_call=FunctionCall(...))`."""
        from services.llm.gemini_provider import _messages_to_gemini_contents

        msgs = [
            {"role": "user", "content": "add two numbers"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "add",
                            "arguments": json.dumps({"a": 14, "b": 28}),
                        },
                    }
                ],
            },
        ]
        _, contents = _messages_to_gemini_contents(msgs, gemini_types)
        assert len(contents) == 2
        assistant_content = contents[1]
        assert assistant_content.role == "model"
        fn_call_part = assistant_content.parts[0]
        assert fn_call_part.function_call is not None
        assert fn_call_part.function_call.name == "add"
        # `args` is exposed as a Struct/dict on the SDK — coerce for the assert.
        args = dict(fn_call_part.function_call.args or {})
        assert args == {"a": 14, "b": 28}

    def test_tool_result_becomes_function_response_keyed_by_name(self, gemini_types):
        """The runner sends `{role:tool, tool_call_id}` — we must look the
        name up from the prior assistant turn so the FunctionResponse names
        the right tool (Gemini correlates by name, not id)."""
        from services.llm.gemini_provider import _messages_to_gemini_contents

        msgs = [
            {"role": "user", "content": "add"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_42",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a":1,"b":2}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_42", "content": "3"},
        ]
        _, contents = _messages_to_gemini_contents(msgs, gemini_types)
        # user, model(fn_call), user(fn_response)
        assert len(contents) == 3
        fn_resp_part = contents[2].parts[0]
        assert fn_resp_part.function_response is not None
        assert fn_resp_part.function_response.name == "add"
        # Content was a bare string — wrapped under {"result": ...}
        assert fn_resp_part.function_response.response == {"result": "3"}

    def test_assistant_text_and_tool_call_coexist_in_one_content(self, gemini_types):
        from services.llm.gemini_provider import _messages_to_gemini_contents

        msgs = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "thinking...",
                "tool_calls": [
                    {
                        "id": "c",
                        "type": "function",
                        "function": {"name": "noop", "arguments": "{}"},
                    }
                ],
            },
        ]
        _, contents = _messages_to_gemini_contents(msgs, gemini_types)
        model_content = contents[1]
        # First part is text, second is function_call.
        assert model_content.parts[0].text == "thinking..."
        assert model_content.parts[1].function_call is not None

    def test_thought_signature_round_trips_on_assistant_tool_call(self, gemini_types):
        """Gemini 3 stamps `thought_signature` on fn_call Parts and rejects
        history that drops it. The translator must restore it (and the
        FunctionCall.id) from `_provider_metadata` on the prior turn."""
        from services.llm.gemini_provider import _messages_to_gemini_contents

        sig = b"\x12\x34\x56\x78fake-sig"
        msgs = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "gemini-issued-id",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a":1,"b":2}'},
                        "_provider_metadata": {
                            "thought_signature": sig,
                            "function_call_id": "gemini-issued-id",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "gemini-issued-id", "content": "3"},
        ]
        _, contents = _messages_to_gemini_contents(msgs, gemini_types)
        model_part = contents[1].parts[0]
        # Signature must be preserved verbatim or Gemini 3 rejects the call.
        assert model_part.thought_signature == sig
        # FunctionCall.id must round-trip so the model can match the response.
        assert model_part.function_call.id == "gemini-issued-id"
        # FunctionResponse must echo the same id for pairing.
        fn_resp = contents[2].parts[0].function_response
        assert fn_resp.id == "gemini-issued-id"

    def test_missing_provider_metadata_still_builds_part_for_2x_models(
        self, gemini_types
    ):
        """Older Gemini (2.5 and earlier) don't issue thought_signatures.
        The translator must produce a valid Part with no signature attached
        rather than crashing."""
        from services.llm.gemini_provider import _messages_to_gemini_contents

        msgs = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a":1,"b":2}'},
                        # No _provider_metadata — 2.x path.
                    }
                ],
            },
        ]
        _, contents = _messages_to_gemini_contents(msgs, gemini_types)
        model_part = contents[1].parts[0]
        assert model_part.function_call.name == "add"
        assert model_part.thought_signature is None

    def test_dict_content_on_tool_role_is_passed_through(self, gemini_types):
        from services.llm.gemini_provider import _messages_to_gemini_contents

        msgs = [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c",
                        "type": "function",
                        "function": {"name": "foo", "arguments": "{}"},
                    }
                ],
            },
            # The runner normally stringifies, but if a handler returned a
            # dict we should pass it through unchanged for richer responses.
            {"role": "tool", "tool_call_id": "c", "content": {"value": 7}},
        ]
        _, contents = _messages_to_gemini_contents(msgs, gemini_types)
        resp = contents[2].parts[0].function_response.response
        assert resp == {"value": 7}


class TestRunSendsTranslatedContents:
    """End-to-end at the provider boundary: stub the SDK, call `run(messages=…)`,
    assert the SDK saw the translated contents list. This is the integration
    seam that the runner relies on; if it breaks, Gemini loops forever."""

    @pytest.mark.asyncio
    async def test_run_with_messages_sends_content_list_to_sdk(
        self, monkeypatch, gemini_types
    ):
        from services.llm import gemini_provider as gp

        captured: dict = {}

        async def _fake_generate(*, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            # Minimal response shape — no candidates so the generator finishes.
            resp = MagicMock()
            resp.candidates = []
            resp.usage_metadata = None
            return resp

        fake_aio = MagicMock()
        fake_aio.models.generate_content = AsyncMock(side_effect=_fake_generate)
        fake_client = MagicMock()
        fake_client.aio = fake_aio

        # Skip credential resolution and SDK construction.
        monkeypatch.setattr(
            gp,
            "resolve_credentials",
            lambda _name: MagicMock(token="fake", mode="api_key", extra={}),
        )
        provider = gp.GeminiProvider()
        provider._client = fake_client

        messages = [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "What is 14 + 28?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "add",
                            "arguments": json.dumps({"a": 14, "b": 28}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "42"},
        ]

        events = []
        async for ev in provider.run(
            prompt="",
            system_prompt="",
            model="gemini-2.5-flash",
            tools=[],
            max_turns=1,
            timeout_seconds=10,
            messages=messages,
        ):
            events.append(ev)

        assert captured, "SDK was not called"
        contents = captured["contents"]
        assert isinstance(contents, list), (
            f"expected a Content list (multi-turn), got {type(contents).__name__}: "
            f"{contents!r} — this is the bug that causes Gemini to ignore history."
        )
        # Shape check: user, model(fn_call), user(fn_response)
        assert [c.role for c in contents] == ["user", "model", "user"]
        assert contents[1].parts[0].function_call.name == "add"
        assert contents[2].parts[0].function_response.name == "add"
        # System instruction was lifted out of the message list.
        assert captured["config"].system_instruction == "be terse"

    @pytest.mark.asyncio
    async def test_run_without_messages_falls_back_to_prompt(
        self, monkeypatch, gemini_types
    ):
        """Back-compat: callers that pass `prompt=` and no `messages=` still
        get the old single-string `contents` shape so they don't break."""
        from services.llm import gemini_provider as gp

        captured: dict = {}

        async def _fake_generate(*, model, contents, config):
            captured["contents"] = contents
            resp = MagicMock()
            resp.candidates = []
            resp.usage_metadata = None
            return resp

        fake_aio = MagicMock()
        fake_aio.models.generate_content = AsyncMock(side_effect=_fake_generate)
        fake_client = MagicMock()
        fake_client.aio = fake_aio

        monkeypatch.setattr(
            gp,
            "resolve_credentials",
            lambda _name: MagicMock(token="fake", mode="api_key", extra={}),
        )
        provider = gp.GeminiProvider()
        provider._client = fake_client

        async for _ in provider.run(
            prompt="hello there",
            system_prompt="be terse",
            model="gemini-2.5-flash",
            tools=[],
        ):
            pass

        assert captured["contents"] == "hello there"
