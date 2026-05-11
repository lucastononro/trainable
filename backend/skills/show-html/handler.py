"""show-html handler — surface an agent-authored HTML page on the canvas.

The agent writes the HTML (and any companion JS/CSS) into its session
workspace via execute-code (or another tool that touches the volume),
then calls this skill to register the artifact and open the canvas tab.

Safeguards:
- Path must be inside `/sessions/{session_id}/` (no cross-session reads,
  no traversal, no `/datasets/...` reads).
- File must already exist on the volume and be `.html`.
- Hard cap of 10 MB per artifact — above that, the handler rejects and
  hints at splitting / externalizing assets.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import time

from services.canvas import publish_canvas_html
from services.volume import read_volume_file_async, reload_volume_async

logger = logging.getLogger(__name__)

_MAX_HTML_BYTES = 10 * 1024 * 1024  # 10 MB hard cap per HTML artifact
_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_key(raw: str) -> str:
    cleaned = _KEY_SAFE.sub("_", raw).strip("._-")
    return cleaned or "html"


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        path_arg = str(args.get("path") or "").strip()
        title_arg = (args.get("title") or "").strip() or None
        key_arg = (args.get("key") or "").strip() or None

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "show-html",
                    "input": {
                        "path": path_arg,
                        "title": title_arg or "(default)",
                        "key": key_arg or "(default)",
                    },
                },
                role="tool",
            )

        def _error(msg: str) -> dict:
            if publish_fn:
                # fire-and-forget: caller awaits us, so we still return
                # the dict, but emit the close event for the tool card.
                pass
            return {
                "content": [{"type": "text", "text": f"show-html failed: {msg}"}],
                "is_error": True,
            }

        async def _emit_end(text: str, is_error: bool):
            if publish_fn:
                await publish_fn(
                    session_id,
                    "tool_end",
                    {"tool": "show-html", "output": text, "is_error": is_error},
                    role="tool",
                )

        if not path_arg:
            response = _error("`path` is required.")
            await _emit_end(response["content"][0]["text"], True)
            return response

        # Normalize + validate path scoping. The path must live under the
        # caller's own session workspace — no traversal, no peeking into
        # another session, no reading from /datasets.
        normalized = posixpath.normpath(path_arg)
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        workspace = f"/sessions/{session_id}"
        if not normalized.startswith(workspace + "/"):
            response = _error(
                f"Path must be inside {workspace}/. Got: {path_arg}. "
                f"Write the HTML to your session workspace first "
                f"(e.g. /data{workspace}/canvas/index.html inside execute-code)."
            )
            await _emit_end(response["content"][0]["text"], True)
            return response
        if ".." in normalized.split("/"):
            response = _error("Path traversal ('..') is not allowed.")
            await _emit_end(response["content"][0]["text"], True)
            return response
        if not normalized.lower().endswith(".html"):
            response = _error("Path must end in .html — the iframe expects HTML.")
            await _emit_end(response["content"][0]["text"], True)
            return response

        try:
            await reload_volume_async()
            raw = await read_volume_file_async(normalized)
        except Exception as e:
            response = _error(f"Could not read {normalized}: {e}")
            await _emit_end(response["content"][0]["text"], True)
            return response

        size = len(raw)
        if size > _MAX_HTML_BYTES:
            response = _error(
                f"HTML payload {size} bytes exceeds the {_MAX_HTML_BYTES} byte limit. "
                "Split the page, or move large assets (datasets, base64-encoded "
                "images) out into companion files referenced via "
                "/api/files/raw?path=…"
            )
            await _emit_end(response["content"][0]["text"], True)
            return response

        # Compute key + title. Default both to the filename stem so the
        # agent can call show-html(path=…) and get a sensible label.
        stem, _ = os.path.splitext(os.path.basename(normalized))
        key = _safe_key(key_arg or stem)
        title = title_arg or stem

        try:
            payload = await publish_canvas_html(
                session_id,
                key=key,
                title=title,
                path=normalized,
                size=size,
                ts=time.time(),
                stage=None,
            )
        except Exception as e:
            logger.exception("publish_canvas_html failed")
            response = _error(f"Publish failed: {e}")
            await _emit_end(response["content"][0]["text"], True)
            return response

        summary = {
            "key": payload["key"],
            "title": payload["title"],
            "path": payload["path"],
            "size": payload["size"],
        }
        text = (
            f"Canvas tab '{title}' opened ({size:,} bytes). "
            "Iframe is sandboxed: scripts run but cannot fetch your API or read app cookies.\n\n"
            + json.dumps(summary, indent=2)
        )
        await _emit_end(f"Opened '{title}' on the canvas.", False)
        return {"content": [{"type": "text", "text": text}]}

    return handler
