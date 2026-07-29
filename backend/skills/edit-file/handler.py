"""edit-file skill — change part of an existing file in place.

Reads via services.volume.read_volume_file_async, applies the patch,
writes back via write_to_volume. v1 supports two modes: exact-anchor
`replace` (old must match exactly once) and whole-file `overwrite`.
Emits `file_updated`, never `file_created` — the file must already
exist. Does NOT write into `scripts/` — authoring is not an execution
step.
"""

from __future__ import annotations

import json
import logging
import time

from services.skills.state import _known_files, resolve_session_path
from services.volume import read_volume_file_async, write_to_volume

logger = logging.getLogger(__name__)


def create_handler(session_id: str, stage: str = None, publish_fn=None, **kwargs):
    """Factory: create an edit_file handler bound to a session/stage."""

    async def handler(args: dict):
        start = time.time()
        raw_path = args.get("path") if isinstance(args, dict) else None
        mode = (args.get("mode") or "replace") if isinstance(args, dict) else "replace"

        try:
            path, _ = resolve_session_path(session_id, raw_path)
        except ValueError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        if mode not in ("replace", "overwrite"):
            msg = f"`mode` must be 'replace' or 'overwrite', got {mode!r}."
            return {"content": [{"type": "text", "text": msg}], "is_error": True}

        await publish_fn(
            session_id,
            "tool_start",
            {"tool": "edit_file", "input": {"path": path, "mode": mode}},
            role="tool",
        )

        async def _fail(msg: str):
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "edit_file",
                    "output": msg,
                    "duration": round(time.time() - start, 1),
                },
                role="tool",
            )
            return {"content": [{"type": "text", "text": msg}], "is_error": True}

        try:
            raw = await read_volume_file_async(path)
        except Exception:
            return await _fail(
                f"File not found: {path}. edit-file only modifies existing "
                "files — use write-file to create one."
            )
        try:
            current = raw.decode("utf-8")
        except UnicodeDecodeError:
            return await _fail(f"File is not UTF-8 text: {path}.")

        if mode == "replace":
            old = args.get("old")
            new = args.get("new")
            if not isinstance(old, str) or not old:
                return await _fail("`old` must be a non-empty string (mode=replace).")
            if not isinstance(new, str):
                return await _fail("`new` must be a string (mode=replace).")
            matches = current.count(old)
            if matches == 0:
                return await _fail(
                    f"Anchor not found in {path}. Re-read the file and "
                    "provide an exact `old` string."
                )
            if matches > 1:
                return await _fail(
                    f"Anchor matches {matches} times in {path} — the edit "
                    "must be unambiguous. Provide a longer, unique `old` "
                    "string."
                )
            updated = current.replace(old, new, 1)
            replacements = 1
        else:
            content = args.get("content")
            if not isinstance(content, str):
                return await _fail("`content` must be a string (mode=overwrite).")
            updated = content
            replacements = 0

        try:
            await write_to_volume(updated, path)
        except Exception as e:
            logger.exception("edit_file failed for %s", path)
            return await _fail(f"Failed to write {path}: {e}")

        name = path.rsplit("/", 1)[-1]
        _known_files.setdefault(session_id, set()).add(path)
        await publish_fn(
            session_id,
            "file_updated",
            {"path": path, "name": name, "type": "file", "stage": stage},
        )

        result = {
            "ok": True,
            "path": path,
            "bytes_written": len(updated.encode("utf-8")),
            "replacements": replacements,
        }
        await publish_fn(
            session_id,
            "tool_end",
            {
                "tool": "edit_file",
                "output": f"Updated {path} ({result['bytes_written']} bytes, "
                f"{replacements} replacement(s))",
                "duration": round(time.time() - start, 1),
            },
            role="tool",
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    return handler
