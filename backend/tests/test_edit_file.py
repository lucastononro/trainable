"""Tests for the edit-file skill handler (issue #85)."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

HANDLER_PATH = (
    Path(__file__).resolve().parents[1] / "skills" / "edit-file" / "handler.py"
)


def _load_handler_module():
    spec = importlib.util.spec_from_file_location("edit_file_handler", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler_mod():
    mod = _load_handler_module()
    mod.write_to_volume = AsyncMock()
    mod.read_volume_file_async = AsyncMock(return_value=b"def load():\n    return 1\n")
    return mod


class _PublishRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event_type, data, role=None, **kwargs):
        self.events.append({"type": event_type, "data": data, "role": role})

    def of_type(self, event_type):
        return [e for e in self.events if e["type"] == event_type]


def _make_handler(mod, session_id="sess-e1"):
    publish = _PublishRecorder()
    handler = mod.create_handler(
        session_id=session_id, stage="data_prep", publish_fn=publish
    )
    return handler, publish


class TestReplaceMode:
    @pytest.mark.asyncio
    async def test_happy_path_mutates_and_emits_file_updated(self, handler_mod):
        handler, publish = _make_handler(handler_mod)
        result = await handler(
            {
                "path": "src/loaders.py",
                "mode": "replace",
                "old": "def load():",
                "new": "def load(n=1):",
            }
        )
        assert not result.get("is_error")
        payload = json.loads(result["content"][0]["text"])
        assert payload["ok"] is True
        assert payload["replacements"] == 1

        handler_mod.write_to_volume.assert_awaited_once_with(
            "def load(n=1):\n    return 1\n", "/sessions/sess-e1/src/loaders.py"
        )
        updated = publish.of_type("file_updated")
        assert len(updated) == 1
        assert updated[0]["data"]["path"] == "/sessions/sess-e1/src/loaders.py"
        # edit-file never creates and never touches the audit log.
        assert publish.of_type("file_created") == []
        for call in handler_mod.write_to_volume.await_args_list:
            assert "/scripts/" not in call.args[1]

    @pytest.mark.asyncio
    async def test_anchor_not_found(self, handler_mod):
        handler, _ = _make_handler(handler_mod)
        result = await handler(
            {"path": "src/loaders.py", "old": "def missing():", "new": "x"}
        )
        assert result["is_error"] is True
        assert "Anchor not found" in result["content"][0]["text"]
        handler_mod.write_to_volume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_anchor_matching_twice_rejected(self, handler_mod):
        handler_mod.read_volume_file_async = AsyncMock(
            return_value=b"return 1\nreturn 1\n"
        )
        handler, _ = _make_handler(handler_mod)
        result = await handler(
            {"path": "src/loaders.py", "old": "return 1", "new": "return 2"}
        )
        assert result["is_error"] is True
        assert "matches 2 times" in result["content"][0]["text"]
        handler_mod.write_to_volume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_replace_is_default_mode(self, handler_mod):
        handler, _ = _make_handler(handler_mod)
        result = await handler(
            {"path": "src/loaders.py", "old": "return 1", "new": "return 2"}
        )
        assert not result.get("is_error")
        handler_mod.write_to_volume.assert_awaited_once_with(
            "def load():\n    return 2\n", "/sessions/sess-e1/src/loaders.py"
        )


class TestOverwriteMode:
    @pytest.mark.asyncio
    async def test_overwrite_replaces_whole_file(self, handler_mod):
        handler, publish = _make_handler(handler_mod)
        result = await handler(
            {"path": "src/loaders.py", "mode": "overwrite", "content": "X = 1\n"}
        )
        assert not result.get("is_error")
        payload = json.loads(result["content"][0]["text"])
        assert payload["replacements"] == 0
        handler_mod.write_to_volume.assert_awaited_once_with(
            "X = 1\n", "/sessions/sess-e1/src/loaders.py"
        )
        assert len(publish.of_type("file_updated")) == 1


class TestFailures:
    @pytest.mark.asyncio
    async def test_missing_file_is_error_never_creates(self, handler_mod):
        handler_mod.read_volume_file_async = AsyncMock(side_effect=FileNotFoundError)
        handler, publish = _make_handler(handler_mod)
        result = await handler({"path": "src/nope.py", "old": "a", "new": "b"})
        assert result["is_error"] is True
        assert "write-file" in result["content"][0]["text"]
        handler_mod.write_to_volume.assert_not_awaited()
        assert publish.of_type("file_created") == []
        assert publish.of_type("file_updated") == []

    @pytest.mark.asyncio
    async def test_invalid_path_rejected(self, handler_mod):
        handler, _ = _make_handler(handler_mod)
        result = await handler({"path": "/etc/passwd", "old": "a", "new": "b"})
        assert result["is_error"] is True
        handler_mod.write_to_volume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_mode_rejected(self, handler_mod):
        handler, _ = _make_handler(handler_mod)
        result = await handler({"path": "src/x.py", "mode": "fuzzy"})
        assert result["is_error"] is True
