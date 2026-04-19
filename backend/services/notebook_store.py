"""Load/save per-session `.ipynb` files on the Modal Volume.

Each session owns a `notebooks/` folder under its workspace and can contain
many notebooks, identified by a slug name (e.g. `data-overview`, `tuning`).
The notebook at `/sessions/{id}/notebooks/{name}.ipynb` is the source of
truth on disk; an in-memory copy (keyed by `(session_id, name)`) is the
authoritative live document during a run — backend is the sole writer.

Single session-level kernel still executes every cell; notebooks in the
same session share variable state. That matches the "shared scratch" mental
model we want for ML work, not JupyterLab's one-kernel-per-notebook default.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from typing import Optional

import nbformat

from services.volume import (
    read_volume_file_async,
    upload_to_volume,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Paths + name sanitisation
# -----------------------------------------------------------------------------

DEFAULT_NOTEBOOK_NAME = "scratch"
_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


def sanitize_name(raw: Optional[str]) -> str:
    """Slug a caller-supplied notebook name.

    Keeps alphanumerics, `-`, `_`. Falls back to `DEFAULT_NOTEBOOK_NAME` if
    the result is empty. Capped at 64 chars — nothing upstream enforces a
    shorter bound, but a longer path breaks a lot of file browsers.
    """
    if not raw:
        return DEFAULT_NOTEBOOK_NAME
    raw = raw.strip()
    if raw.lower().endswith(".ipynb"):
        raw = raw[:-6]
    cleaned = _NAME_RE.sub("-", raw).strip("-_")
    cleaned = cleaned[:64]
    return cleaned or DEFAULT_NOTEBOOK_NAME


def notebooks_dir(session_id: str) -> str:
    return f"/sessions/{session_id}/notebooks"


def notebook_path(session_id: str, name: str) -> str:
    return f"{notebooks_dir(session_id)}/{sanitize_name(name)}.ipynb"


def parse_notebook_path(path: str) -> Optional[tuple[str, str]]:
    """Reverse `notebook_path`. Returns `(session_id, name)` or None."""
    m = re.match(r"^/sessions/([^/]+)/notebooks/([^/]+)\.ipynb$", path)
    if not m:
        return None
    return m.group(1), m.group(2)


# -----------------------------------------------------------------------------
# Cache + locks (keyed by session_id + name)
# -----------------------------------------------------------------------------

_Key = tuple[str, str]

_cache: dict[_Key, nbformat.NotebookNode] = {}
_locks: dict[_Key, asyncio.Lock] = {}


def _lock(key: _Key) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


# -----------------------------------------------------------------------------
# CRUD
# -----------------------------------------------------------------------------


async def load(session_id: str, name: str) -> Optional[nbformat.NotebookNode]:
    """Return the cached notebook; fall back to reading from the Volume."""
    key = (session_id, sanitize_name(name))
    if key in _cache:
        return _cache[key]

    path = notebook_path(session_id, name)

    try:
        data = await read_volume_file_async(path)
    except Exception as e:
        logger.debug("No notebook at %s: %s", path, e)
        return None
    try:
        nb = nbformat.reads(data.decode("utf-8"), as_version=4)
    except Exception as e:
        logger.warning("Failed to parse notebook %s: %s", path, e)
        return None
    _cache[key] = nb
    return nb


async def save(
    session_id: str,
    name: str,
    nb: Optional[nbformat.NotebookNode] = None,
) -> None:
    """Write the notebook to the Modal Volume."""
    key = (session_id, sanitize_name(name))
    async with _lock(key):
        if nb is not None:
            _cache[key] = nb
        nb = _cache.get(key)
        if nb is None:
            return
        content = nbformat.writes(nb)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ipynb", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    try:
        await upload_to_volume(tmp, notebook_path(session_id, name))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


async def list_notebooks(session_id: str) -> list[dict]:
    """List all notebooks for a session. Returns `[{name, path, cells}, ...]`.

    Includes any notebook that exists on the Modal Volume OR is in the
    in-memory cache (covers the race where a notebook was just created but
    not yet visible to a `vol.reload()`).
    """
    from services.volume import listdir_async

    results: dict[str, dict] = {}
    prefix = notebooks_dir(session_id)
    try:
        entries = await listdir_async(prefix, recursive=False)
    except Exception as e:
        logger.debug("listdir %s failed: %s", prefix, e)
        entries = []
    for entry in entries:
        p = getattr(entry, "path", "")
        if not p.endswith(".ipynb"):
            continue
        parsed = parse_notebook_path(p)
        if not parsed:
            continue
        _, name = parsed
        results[name] = {"name": name, "path": p, "cells": None}
    from_disk = list(results.values())
    disk_names = {n["name"] for n in from_disk}

    merged = list(from_disk)
    for (sid, name), nb in _cache.items():
        if sid != session_id or name in disk_names:
            continue
        merged.append(
            {"name": name, "path": notebook_path(session_id, name), "cells": None}
        )

    # Attach cell counts from the in-memory cache where we have them.
    for item in merged:
        nb = _cache.get((session_id, item["name"]))
        if nb is not None:
            item["cells"] = len(nb.cells)
    merged.sort(key=lambda x: x["name"])
    return merged


async def append_cell(
    session_id: str,
    name: str,
    cell_type: str,
    source: str,
    after_cell_id: Optional[str] = None,
) -> dict:
    """Insert a cell into the notebook, creating the notebook if missing."""
    if cell_type not in ("code", "markdown"):
        raise ValueError(f"cell_type must be 'code' or 'markdown', got {cell_type!r}")

    name = sanitize_name(name)
    key = (session_id, name)
    nb = await load(session_id, name)
    created_new = nb is None
    if created_new:
        nb = nbformat.v4.new_notebook()

    new_cell = (
        nbformat.v4.new_code_cell(source)
        if cell_type == "code"
        else nbformat.v4.new_markdown_cell(source)
    )

    async with _lock(key):
        if after_cell_id:
            idx = next(
                (
                    i
                    for i, c in enumerate(nb.cells)
                    if c.get("id") == after_cell_id
                ),
                None,
            )
            if idx is not None:
                nb.cells.insert(idx + 1, new_cell)
            else:
                nb.cells.append(new_cell)
        else:
            nb.cells.append(new_cell)
        _cache[key] = nb

    await save(session_id, name)
    return {
        "notebook_name": name,
        "notebook_path": notebook_path(session_id, name),
        "cell_id": new_cell["id"],
        "cell_type": cell_type,
        "created_notebook": created_new,
        "total_cells": len(nb.cells),
    }


async def on_cell_event(
    session_id: str,
    name: str,
    event_type: str,
    payload: dict,
) -> None:
    """Mutate the in-memory notebook in response to a kernel output event."""
    key = (session_id, sanitize_name(name))
    async with _lock(key):
        nb = _cache.get(key)
        if nb is None:
            return
        cell_id = payload.get("cell_id")
        cell = next((c for c in nb.cells if c.get("id") == cell_id), None)
        if cell is None or cell.get("cell_type") != "code":
            return

        if event_type == "cell_started":
            cell["outputs"] = []
            cell["execution_count"] = None
        elif event_type == "cell_stream":
            cell["outputs"].append(
                nbformat.v4.new_output(
                    output_type="stream",
                    name=payload.get("name", "stdout"),
                    text=payload.get("text", ""),
                )
            )
        elif event_type == "cell_display":
            cell["outputs"].append(
                nbformat.v4.new_output(
                    output_type="display_data",
                    data=payload.get("data", {}),
                    metadata=payload.get("metadata", {}),
                )
            )
        elif event_type == "cell_error":
            cell["outputs"].append(
                nbformat.v4.new_output(
                    output_type="error",
                    ename=payload.get("ename", ""),
                    evalue=payload.get("evalue", ""),
                    traceback=payload.get("traceback", []),
                )
            )
        elif event_type == "cell_completed":
            ec = payload.get("exec_count")
            if ec is not None:
                cell["execution_count"] = ec

    if event_type in ("cell_completed", "cell_error"):
        await save(session_id, name)


def apply_source_update(
    session_id: str,
    name: str,
    nb_from_client: nbformat.NotebookNode,
) -> nbformat.NotebookNode:
    """Merge a client PUT (sources + order + types) with server-held outputs."""
    key = (session_id, sanitize_name(name))
    existing = _cache.get(key)
    if existing is None:
        _cache[key] = nb_from_client
        return nb_from_client

    existing_by_id = {c.get("id"): c for c in existing.cells if c.get("id")}
    for cell in nb_from_client.cells:
        cid = cell.get("id")
        old = existing_by_id.get(cid) if cid else None
        if old is not None and cell.get("cell_type") == "code":
            cell["outputs"] = old.get("outputs", [])
            cell["execution_count"] = old.get("execution_count")
    _cache[key] = nb_from_client
    return nb_from_client


# -----------------------------------------------------------------------------
# Agent-facing rendering
# -----------------------------------------------------------------------------


def _src(cell) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else src


def _format_outputs(outputs: list) -> str:
    lines = []
    for o in outputs or []:
        otype = o.get("output_type")
        if otype == "stream":
            text = o.get("text", "")
            if isinstance(text, list):
                text = "".join(text)
            lines.append(f"[{o.get('name', 'stdout')}]\n{text.rstrip()}")
        elif otype in ("display_data", "execute_result"):
            data = o.get("data", {}) or {}
            if "text/plain" in data:
                plain = data["text/plain"]
                if isinstance(plain, list):
                    plain = "".join(plain)
                lines.append(f"[{otype}]\n{plain.rstrip()}")
            elif "image/png" in data:
                lines.append(f"[{otype}] <png image>")
            elif "text/html" in data:
                lines.append(f"[{otype}] <html output>")
        elif otype == "error":
            ename = o.get("ename", "")
            evalue = o.get("evalue", "")
            lines.append(f"[error] {ename}: {evalue}")
    return "\n".join(lines)


async def render_for_agent(
    session_id: str,
    name: str,
    include_outputs: bool = True,
    max_chars_per_cell: int = 4000,
) -> Optional[str]:
    """Render the notebook as compact text for agent consumption."""
    name = sanitize_name(name)
    nb = await load(session_id, name)
    if nb is None:
        return None

    lines = [
        f"# Notebook `{name}` — session {session_id} — {len(nb.cells)} cells",
        "",
    ]
    for i, cell in enumerate(nb.cells):
        ctype = cell.get("cell_type", "?")
        cid = cell.get("id", "")
        if ctype == "code":
            ec = cell.get("execution_count")
            label = f"## Cell {i} [code] id={cid} exec={ec}"
        else:
            label = f"## Cell {i} [{ctype}] id={cid}"
        lines.append(label)
        src = _src(cell)
        if len(src) > max_chars_per_cell:
            src = src[:max_chars_per_cell] + f"\n…(truncated; {len(src)} chars)"
        lines.append("```")
        lines.append(src)
        lines.append("```")
        if include_outputs and ctype == "code":
            outs = _format_outputs(cell.get("outputs", []))
            if outs:
                if len(outs) > max_chars_per_cell:
                    outs = outs[:max_chars_per_cell] + "\n…(truncated)"
                lines.append("outputs:")
                lines.append(outs)
        lines.append("")
    return "\n".join(lines)
