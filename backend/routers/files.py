"""Serve files from Modal Volume (reports, charts, etc.)."""

from __future__ import annotations

import logging
import mimetypes
import posixpath
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from services.volume import listdir_async, read_volume_file_async

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_PREFIXES = ("/sessions/", "/datasets/")


def _validate_path(path: str) -> str:
    """Normalize and validate that a path stays within allowed prefixes."""
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    if not any(
        normalized.startswith(p) or normalized == p.rstrip("/")
        for p in _ALLOWED_PREFIXES
    ):
        raise HTTPException(
            status_code=403, detail="Access denied: path outside allowed directories"
        )
    if ".." in normalized.split("/"):
        raise HTTPException(
            status_code=403, detail="Access denied: path traversal detected"
        )
    return normalized


@router.get("/files/list")
async def list_files(path: str = "/"):
    """List files/dirs in Modal Volume at given path."""
    try:
        path = _validate_path(path)
        entries = []
        for entry in await listdir_async(path, recursive=False):
            entries.append(
                {
                    "path": entry.path,
                    "type": "file" if entry.type.name == "FILE" else "directory",
                }
            )
        return {"path": path, "entries": entries}
    except Exception as e:
        logger.error(f"list_files error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/files/read")
async def read_file(path: str):
    """Read a text file from Modal Volume."""
    try:
        path = _validate_path(path)
        data = await read_volume_file_async(path)
        return {"path": path, "content": data.decode("utf-8", errors="replace")}
    except Exception as e:
        logger.error(f"read_file error: {e}")
        raise HTTPException(status_code=404, detail=str(e))


# CSP for served HTML artifacts (rendered inside a sandboxed iframe on
# the frontend). Allows inline <script>/<style> (Plotly's HTML export
# relies on it) but blocks any outbound network — `connect-src 'none'`
# is the belt-and-suspenders defense even if the iframe sandbox is
# bypassed somehow. CDN allowlist covers the common interactive-viz
# libraries (Plotly, Bokeh, D3) that agents reach for.
_HTML_CSP = (
    "default-src 'none'; "
    # 'self' lets HTML reference companion JS files saved next to it on
    # the volume via absolute /api/files/raw?path=… URLs. 'unsafe-inline'
    # covers <script>…</script> blocks (Plotly's HTML export uses these).
    # CDN allow-list covers the common interactive-viz libs agents reach for.
    "script-src 'self' 'unsafe-inline' https://cdn.plot.ly https://cdn.bokeh.org "
    "https://d3js.org https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https:; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data: https:; "
    # connect-src 'none' is the belt+suspenders: even if a script slips
    # past the allow-list, it cannot fetch/XHR/WebSocket out of the iframe.
    "connect-src 'none'"
    # NOTE: deliberately no `frame-ancestors` directive. `'self'` would
    # resolve to the backend's origin and block embedding from the
    # frontend (different port/origin in dev, same origin in prod via
    # reverse proxy). The iframe sandbox + missing `allow-same-origin`
    # are the actual containment; restricting who can embed serves no
    # additional purpose for this artifact channel.
)


@router.get("/files/raw")
async def raw_file(path: str):
    """Serve a raw file from Modal Volume (images, html artifacts, etc.).

    For text/html responses, attach a strict Content-Security-Policy +
    X-Content-Type-Options: nosniff so agent-generated HTML rendered in
    a sandboxed iframe cannot beacon out to arbitrary hosts and a
    misclassified `.html` cannot be re-interpreted as a different MIME.
    """
    try:
        path = _validate_path(path)
        data = await read_volume_file_async(path)
        mime, _ = mimetypes.guess_type(path)
        headers: dict[str, str] = {}
        if mime == "text/html":
            headers["Content-Security-Policy"] = _HTML_CSP
            headers["X-Content-Type-Options"] = "nosniff"
            headers["Referrer-Policy"] = "no-referrer"
        return Response(
            content=data,
            media_type=mime or "application/octet-stream",
            headers=headers or None,
        )
    except Exception as e:
        logger.error(f"raw_file error: {e}")
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/files/tree")
async def file_tree(root: str = "/"):
    """Return a nested file tree from Modal Volume.

    The root is typically /sessions/{uuid}. We unwrap wrapper directories
    so the UI sees eda/, prep/, train/ at the top level.

    A brand-new session won't have a workspace directory until the first
    agent run creates one — `listdir_async` raises FileNotFoundError in
    that case. Return an empty tree (200) instead of a 500 so the
    frontend's session-load path doesn't have to special-case "not yet
    populated" sessions.
    """
    try:
        root = _validate_path(root)
        entries = await listdir_async(root, recursive=True)
        tree = _build_tree(root, entries)
        tree = _unwrap_tree(tree)
        tree["name"] = "workspace"
        return tree
    except FileNotFoundError:
        return {
            "name": "workspace",
            "path": root,
            "type": "directory",
            "children": [],
        }
    except Exception as e:
        logger.error(f"file_tree error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _build_tree(root: str, entries) -> dict:
    """Convert flat file listing into nested tree structure."""
    root_clean = root.strip("/")
    tree = {
        "name": root_clean.split("/")[-1] or "workspace",
        "path": root,
        "type": "directory",
        "children": [],
    }

    for entry in entries:
        rel = entry.path.lstrip("/")
        if rel.startswith(root_clean + "/"):
            rel = rel[len(root_clean) + 1 :]
        elif rel == root_clean:
            continue
        rel = rel.lstrip("/")
        if not rel:
            continue

        is_file = entry.type.name == "FILE"
        segments = rel.split("/")

        current = tree
        for i, seg in enumerate(segments):
            is_last = i == len(segments) - 1
            if is_last and is_file:
                current["children"].append(
                    {
                        "name": seg,
                        "path": entry.path,
                        "type": "file",
                    }
                )
            else:
                child = next(
                    (
                        c
                        for c in current["children"]
                        if c["name"] == seg and c["type"] == "directory"
                    ),
                    None,
                )
                if child is None:
                    child = {
                        "name": seg,
                        "path": root_clean + "/" + "/".join(segments[: i + 1]),
                        "type": "directory",
                        "children": [],
                    }
                    current["children"].append(child)
                current = child

    _sort_tree(tree)
    return tree


def _sort_tree(node: dict):
    """Recursively sort tree children: dirs first, then files, alphabetically."""
    if "children" not in node:
        return
    for child in node["children"]:
        _sort_tree(child)
    node["children"].sort(
        key=lambda c: (0 if c["type"] == "directory" else 1, c["name"])
    )


def _is_infra_name(name: str) -> bool:
    return name == "sessions" or bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", name))


def _unwrap_tree(tree: dict) -> dict:
    """Strip infrastructure directories (sessions, UUIDs) from tree root."""
    while (
        tree.get("children")
        and len(tree["children"]) == 1
        and tree["children"][0].get("type") == "directory"
        and _is_infra_name(tree["children"][0].get("name", ""))
    ):
        only_child = tree["children"][0]
        tree["children"] = only_child.get("children", [])
    return tree
