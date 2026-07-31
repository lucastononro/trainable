"""Tests for notebook_store.edit_cell / delete_cell (issue #85).

The agent-facing per-cell surface: edit_cell preserves outputs (same as
apply_source_update's carry-over for the frontend PUT path); delete_cell
persists the removal to the on-volume .ipynb.
"""

import nbformat
import pytest
from unittest.mock import AsyncMock

from services import notebook_store


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch):
    """Isolate the process-global notebook cache/locks; capture saves."""
    notebook_store._cache.clear()
    notebook_store._locks.clear()
    saves = []
    upload = AsyncMock()
    monkeypatch.setattr(notebook_store, "upload_to_volume", upload)
    monkeypatch.setattr(
        notebook_store,
        "read_volume_file_async",
        AsyncMock(side_effect=FileNotFoundError),
    )
    yield saves
    notebook_store._cache.clear()
    notebook_store._locks.clear()


def _seed_notebook(session_id="sess-n1", name="scratch"):
    """Seed the cache with a notebook: one markdown + two code cells."""
    nb = nbformat.v4.new_notebook()
    md = nbformat.v4.new_markdown_cell("# title")
    c1 = nbformat.v4.new_code_cell("x = 1")
    c2 = nbformat.v4.new_code_cell("print(x)")
    c1["outputs"] = [
        nbformat.v4.new_output(output_type="stream", name="stdout", text="1\n")
    ]
    c1["execution_count"] = 3
    nb.cells = [md, c1, c2]
    notebook_store._cache[(session_id, name)] = nb
    return nb, md, c1, c2


class TestEditCell:
    @pytest.mark.asyncio
    async def test_edit_preserves_outputs(self):
        nb, _, c1, _ = _seed_notebook()
        info = await notebook_store.edit_cell("sess-n1", "scratch", c1["id"], "x = 2")
        assert info["cell_id"] == c1["id"]
        assert info["source_len"] == len("x = 2")
        cell = next(c for c in nb.cells if c["id"] == c1["id"])
        assert cell["source"] == "x = 2"
        # Outputs + execution count carried over untouched.
        assert cell["outputs"] == c1["outputs"]
        assert cell["execution_count"] == 3

    @pytest.mark.asyncio
    async def test_edit_persists_to_volume(self):
        _, _, c1, _ = _seed_notebook()
        await notebook_store.edit_cell("sess-n1", "scratch", c1["id"], "x = 9")
        notebook_store.upload_to_volume.assert_awaited_once()
        _, vol_path = notebook_store.upload_to_volume.await_args.args
        assert vol_path == "/sessions/sess-n1/notebooks/scratch.ipynb"

    @pytest.mark.asyncio
    async def test_edit_unknown_cell_raises(self):
        _seed_notebook()
        with pytest.raises(ValueError, match="not found"):
            await notebook_store.edit_cell("sess-n1", "scratch", "nope", "x")

    @pytest.mark.asyncio
    async def test_edit_unknown_notebook_raises(self):
        with pytest.raises(ValueError, match="Notebook 'ghost' not found"):
            await notebook_store.edit_cell("sess-n1", "ghost", "c", "x")

    @pytest.mark.asyncio
    async def test_edit_can_convert_cell_type(self):
        nb, _, c1, _ = _seed_notebook()
        await notebook_store.edit_cell(
            "sess-n1", "scratch", c1["id"], "now prose", cell_type="markdown"
        )
        cell = next(c for c in nb.cells if c["id"] == c1["id"])
        assert cell["cell_type"] == "markdown"
        assert "outputs" not in cell
        assert "execution_count" not in cell

    @pytest.mark.asyncio
    async def test_edit_invalid_cell_type_raises(self):
        _, _, c1, _ = _seed_notebook()
        with pytest.raises(ValueError):
            await notebook_store.edit_cell(
                "sess-n1", "scratch", c1["id"], "x", cell_type="raw"
            )


class TestDeleteCell:
    @pytest.mark.asyncio
    async def test_delete_removes_and_persists(self):
        nb, md, c1, c2 = _seed_notebook()
        info = await notebook_store.delete_cell("sess-n1", "scratch", c1["id"])
        assert info["cell_id"] == c1["id"]
        assert info["remaining_cells"] == 2
        assert [c["id"] for c in nb.cells] == [md["id"], c2["id"]]
        notebook_store.upload_to_volume.assert_awaited_once()
        _, vol_path = notebook_store.upload_to_volume.await_args.args
        assert vol_path == "/sessions/sess-n1/notebooks/scratch.ipynb"

    @pytest.mark.asyncio
    async def test_delete_unknown_cell_raises(self):
        _seed_notebook()
        with pytest.raises(ValueError, match="not found"):
            await notebook_store.delete_cell("sess-n1", "scratch", "nope")

    @pytest.mark.asyncio
    async def test_delete_unknown_notebook_raises(self):
        with pytest.raises(ValueError, match="Notebook 'ghost' not found"):
            await notebook_store.delete_cell("sess-n1", "ghost", "c")
