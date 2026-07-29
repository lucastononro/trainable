"""Tests for the edit-notebook-cell skill handler (issue #85)."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nbformat
import pytest

HANDLER_PATH = (
    Path(__file__).resolve().parents[1] / "skills" / "edit-notebook-cell" / "handler.py"
)


def _load_handler_module():
    spec = importlib.util.spec_from_file_location(
        "edit_notebook_cell_handler", HANDLER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler_mod():
    mod = _load_handler_module()
    mod.broadcaster = SimpleNamespace(publish=AsyncMock())
    return mod


@pytest.fixture
def seeded_store(monkeypatch):
    """Real notebook_store with an isolated cache and captured saves."""
    from services import notebook_store

    notebook_store._cache.clear()
    notebook_store._locks.clear()
    monkeypatch.setattr(notebook_store, "upload_to_volume", AsyncMock())
    monkeypatch.setattr(
        notebook_store,
        "read_volume_file_async",
        AsyncMock(side_effect=FileNotFoundError),
    )
    nb = nbformat.v4.new_notebook()
    cell = nbformat.v4.new_code_cell("x = 1")
    cell["outputs"] = [
        nbformat.v4.new_output(output_type="stream", name="stdout", text="1\n")
    ]
    nb.cells = [cell]
    notebook_store._cache[("sess-nc1", "scratch")] = nb
    yield notebook_store, cell
    notebook_store._cache.clear()
    notebook_store._locks.clear()


class _PublishRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event_type, data, role=None, **kwargs):
        self.events.append({"type": event_type, "data": data, "role": role})


def _make_handler(mod, session_id="sess-nc1"):
    publish = _PublishRecorder()
    handler = mod.create_handler(session_id=session_id, publish_fn=publish)
    return handler, publish


@pytest.mark.asyncio
async def test_happy_path_edits_and_broadcasts(handler_mod, seeded_store):
    store, cell = seeded_store
    handler, publish = _make_handler(handler_mod)
    result = await handler({"cell_id": cell["id"], "source": "x = 2"})
    assert not result.get("is_error")
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["cell_id"] == cell["id"]
    assert payload["source_len"] == len("x = 2")

    # Source updated, outputs preserved.
    assert cell["source"] == "x = 2"
    assert cell["outputs"]

    # UI live-update event.
    handler_mod.broadcaster.publish.assert_awaited_once()
    _, event = handler_mod.broadcaster.publish.await_args.args
    assert event["type"] == "notebook.structure.changed"
    assert event["data"]["reason"] == "agent_edit"
    assert event["data"]["cell_id"] == cell["id"]

    # tool_start/tool_end pair emitted in order.
    kinds = [e["type"] for e in publish.events]
    assert kinds[0] == "tool_start"
    assert kinds[-1] == "tool_end"


@pytest.mark.asyncio
async def test_unknown_cell_is_error(handler_mod, seeded_store):
    handler, _ = _make_handler(handler_mod)
    result = await handler({"cell_id": "nope", "source": "x"})
    assert result["is_error"] is True
    assert "not found" in result["content"][0]["text"]
    handler_mod.broadcaster.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_string_source_rejected(handler_mod, seeded_store):
    _, cell = seeded_store
    handler, _ = _make_handler(handler_mod)
    result = await handler({"cell_id": cell["id"], "source": 5})
    assert result["is_error"] is True
