"""Tests for the rerun-notebook-cell skill handler (issue #85)."""

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import nbformat
import pytest

HANDLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "rerun-notebook-cell"
    / "handler.py"
)


def _load_handler_module():
    spec = importlib.util.spec_from_file_location(
        "rerun_notebook_cell_handler", HANDLER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler_mod():
    mod = _load_handler_module()
    mod.kernel_manager = AsyncMock()
    mod.kernel_manager.execute_and_wait = AsyncMock(
        return_value={"exec_count": 7, "duration_ms": 42, "had_error": False}
    )
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
    md = nbformat.v4.new_markdown_cell("# notes")
    code = nbformat.v4.new_code_cell("from loaders import load\ndf = load()")
    code["outputs"] = [
        nbformat.v4.new_output(output_type="stream", name="stdout", text="stale\n")
    ]
    nb.cells = [md, code]
    notebook_store._cache[("sess-nc3", "scratch")] = nb
    yield notebook_store, md, code
    notebook_store._cache.clear()
    notebook_store._locks.clear()


class _PublishRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event_type, data, role=None, **kwargs):
        self.events.append({"type": event_type, "data": data, "role": role})


def _make_handler(mod, session_id="sess-nc3"):
    publish = _PublishRecorder()
    handler = mod.create_handler(session_id=session_id, publish_fn=publish)
    return handler, publish


@pytest.mark.asyncio
async def test_happy_path_reexecutes_existing_cell(handler_mod, seeded_store):
    _, _, code = seeded_store
    handler, publish = _make_handler(handler_mod)
    result = await handler({"cell_id": code["id"]})
    assert not result.get("is_error")
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["cell_id"] == code["id"]
    assert payload["exec_count"] == 7
    assert payload["duration_ms"] == 42
    assert payload["had_error"] is False

    # Re-executed against the persistent kernel with the cell's CURRENT
    # source and id — no new cell appended.
    args = handler_mod.kernel_manager.execute_and_wait.await_args
    assert args.args[0] == "sess-nc3"
    assert args.args[1] == code["id"]
    assert args.args[2] == "from loaders import load\ndf = load()"
    assert args.kwargs["notebook_name"] == "scratch"

    kinds = [e["type"] for e in publish.events]
    assert kinds[0] == "tool_start"
    assert kinds[-1] == "tool_end"


@pytest.mark.asyncio
async def test_unknown_cell_is_error(handler_mod, seeded_store):
    handler, _ = _make_handler(handler_mod)
    result = await handler({"cell_id": "nope"})
    assert result["is_error"] is True
    assert "not found" in result["content"][0]["text"]
    handler_mod.kernel_manager.execute_and_wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_markdown_cell_rejected(handler_mod, seeded_store):
    _, md, _ = seeded_store
    handler, _ = _make_handler(handler_mod)
    result = await handler({"cell_id": md["id"]})
    assert result["is_error"] is True
    assert "markdown" in result["content"][0]["text"]
    handler_mod.kernel_manager.execute_and_wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_is_error(handler_mod, seeded_store):
    _, _, code = seeded_store
    handler_mod.kernel_manager.execute_and_wait = AsyncMock(
        side_effect=asyncio.TimeoutError
    )
    handler, _ = _make_handler(handler_mod)
    result = await handler({"cell_id": code["id"], "timeout_seconds": 10})
    assert result["is_error"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is False
    assert "timeout" in payload["error"].lower()
