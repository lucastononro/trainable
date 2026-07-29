"""Tests for the write-file skill handler + session path resolution (issue #85)."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.skills.state import resolve_session_path

HANDLER_PATH = (
    Path(__file__).resolve().parents[1] / "skills" / "write-file" / "handler.py"
)


def _load_handler_module():
    spec = importlib.util.spec_from_file_location("write_file_handler", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler_mod(monkeypatch):
    mod = _load_handler_module()
    mod.write_to_volume = AsyncMock()
    # Default: nothing exists on the volume.
    mod.read_volume_file_async = AsyncMock(side_effect=FileNotFoundError)
    return mod


class _PublishRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event_type, data, role=None, **kwargs):
        self.events.append({"type": event_type, "data": data, "role": role})

    def of_type(self, event_type):
        return [e for e in self.events if e["type"] == event_type]


def _make_handler(mod, session_id="sess-w1"):
    publish = _PublishRecorder()
    handler = mod.create_handler(session_id=session_id, stage="eda", publish_fn=publish)
    return handler, publish


class TestPathResolution:
    def test_relative_path(self):
        vol, rel = resolve_session_path("s1", "src/loaders.py")
        assert vol == "/sessions/s1/src/loaders.py"
        assert rel == "src/loaders.py"

    def test_volume_absolute_path(self):
        vol, rel = resolve_session_path("s1", "/sessions/s1/src/loaders.py")
        assert vol == "/sessions/s1/src/loaders.py"
        assert rel == "src/loaders.py"

    def test_sandbox_absolute_path(self):
        vol, rel = resolve_session_path("s1", "/data/sessions/s1/src/loaders.py")
        assert vol == "/sessions/s1/src/loaders.py"
        assert rel == "src/loaders.py"

    def test_dot_slash_relative(self):
        vol, _ = resolve_session_path("s1", "./src/loaders.py")
        assert vol == "/sessions/s1/src/loaders.py"

    def test_other_absolute_path_rejected(self):
        with pytest.raises(ValueError):
            resolve_session_path("s1", "/etc/passwd")

    def test_other_session_rejected(self):
        with pytest.raises(ValueError):
            resolve_session_path("s1", "/sessions/other/src/x.py")

    def test_parent_escape_rejected(self):
        for raw in ("../x.py", "src/../../x.py", "src/..", ".."):
            with pytest.raises(ValueError):
                resolve_session_path("s1", raw)

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            resolve_session_path("s1", "  ")


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_new_file_emits_file_created_and_writes(self, handler_mod):
        handler, publish = _make_handler(handler_mod)
        result = await handler({"path": "src/loaders.py", "content": "def load(): ..."})
        assert not result.get("is_error")
        payload = json.loads(result["content"][0]["text"])
        assert payload["ok"] is True
        assert payload["path"] == "/sessions/sess-w1/src/loaders.py"
        assert payload["created"] is True
        assert payload["bytes_written"] == len("def load(): ...")

        handler_mod.write_to_volume.assert_awaited_once_with(
            "def load(): ...", "/sessions/sess-w1/src/loaders.py"
        )
        created = publish.of_type("file_created")
        assert len(created) == 1
        assert created[0]["data"]["path"] == "/sessions/sess-w1/src/loaders.py"
        # Authoring is not an execution step — nothing under scripts/.
        assert "/scripts/" not in payload["path"]
        for call in handler_mod.write_to_volume.await_args_list:
            assert "/scripts/" not in call.args[1]

    @pytest.mark.asyncio
    async def test_existing_file_emits_file_updated(self, handler_mod):
        handler_mod.read_volume_file_async = AsyncMock(return_value=b"old")
        handler, publish = _make_handler(handler_mod)
        result = await handler({"path": "src/loaders.py", "content": "new"})
        assert not result.get("is_error")
        payload = json.loads(result["content"][0]["text"])
        assert payload["created"] is False
        assert len(publish.of_type("file_updated")) == 1
        assert len(publish.of_type("file_created")) == 0

    @pytest.mark.asyncio
    async def test_overwrite_false_refuses_clobber(self, handler_mod):
        handler_mod.read_volume_file_async = AsyncMock(return_value=b"old")
        handler, _ = _make_handler(handler_mod)
        result = await handler(
            {"path": "src/loaders.py", "content": "new", "overwrite": False}
        )
        assert result["is_error"] is True
        assert "overwrite=false" in result["content"][0]["text"]
        handler_mod.write_to_volume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_path_rejected(self, handler_mod):
        handler, publish = _make_handler(handler_mod)
        result = await handler({"path": "/etc/passwd", "content": "x"})
        assert result["is_error"] is True
        handler_mod.write_to_volume.assert_not_awaited()
        assert publish.of_type("tool_start") == []

    @pytest.mark.asyncio
    async def test_non_string_content_rejected(self, handler_mod):
        handler, _ = _make_handler(handler_mod)
        result = await handler({"path": "src/x.py", "content": 42})
        assert result["is_error"] is True
        handler_mod.write_to_volume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_absolute_session_path_accepted(self, handler_mod):
        handler, _ = _make_handler(handler_mod)
        result = await handler(
            {"path": "/sessions/sess-w1/src/loaders.py", "content": "x"}
        )
        assert not result.get("is_error")
        handler_mod.write_to_volume.assert_awaited_once_with(
            "x", "/sessions/sess-w1/src/loaders.py"
        )
