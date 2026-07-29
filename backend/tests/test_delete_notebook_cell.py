"""Tests for the delete-notebook-cell skill handler (issue #85)."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nbformat
import pytest

HANDLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "delete-notebook-cell"
    / "handler.py"
)


def _load_handler_module():
    spec = importlib.util.spec_from_file_location(
        "delete_notebook_cell_handler", HANDLER_PATH
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
    keep = nbformat.v4.new_code_cell("x = 1")
    drop = nbformat.v4.new_code_cell("print('dead end')")
    nb.cells = [keep, drop]
    notebook_store._cache[("sess-nc2", "scratch")] = nb
    yield notebook_store, keep, drop
    notebook_store._cache.clear()
    notebook_store._locks.clear()


class _PublishRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event_type, data, role=None, **kwargs):
        self.events.append({"type": event_type, "data": data, "role": role})


@pytest.mark.asyncio
async def test_happy_path_deletes_and_broadcasts(handler_mod, seeded_store):
    store, keep, drop = seeded_store
    publish = _PublishRecorder()
    handler = handler_mod.create_handler(session_id="sess-nc2", publish_fn=publish)
    result = await handler({"cell_id": drop["id"]})
    assert not result.get("is_error")
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["cell_id"] == drop["id"]
    assert payload["remaining_cells"] == 1

    nb = store._cache[("sess-nc2", "scratch")]
    assert [c["id"] for c in nb.cells] == [keep["id"]]

    # On-volume .ipynb updated.
    store.upload_to_volume.assert_awaited_once()

    # UI live-update event.
    _, event = handler_mod.broadcaster.publish.await_args.args
    assert event["type"] == "notebook.structure.changed"
    assert event["data"]["reason"] == "agent_delete"
    assert event["data"]["total_cells"] == 1


@pytest.mark.asyncio
async def test_unknown_cell_is_error(handler_mod, seeded_store):
    publish = _PublishRecorder()
    handler = handler_mod.create_handler(session_id="sess-nc2", publish_fn=publish)
    result = await handler({"cell_id": "nope"})
    assert result["is_error"] is True
    assert "not found" in result["content"][0]["text"]
    handler_mod.broadcaster.publish.assert_not_awaited()
