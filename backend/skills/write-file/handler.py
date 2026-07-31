"""write-file skill — author a file in the session workspace.

Wraps services.volume.write_to_volume. Emits `file_created` (new file) or
`file_updated` (overwrite) so the UI can distinguish authoring from
execution. Deliberately does NOT write into `scripts/` — that directory
is the audit log of what ran, and authoring is not an execution step.
"""

from __future__ import annotations

import json
import logging
import time

from services.skills.state import _known_files, resolve_session_path
from services.volume import read_volume_file_async, write_to_volume

logger = logging.getLogger(__name__)


async def _file_exists(path: str) -> bool:
    try:
        await read_volume_file_async(path)
        return True
    except Exception:
        return False


def create_handler(session_id: str, stage: str = None, publish_fn=None, **kwargs):
    """Factory: create a write_file handler bound to a session/stage."""

    async def handler(args: dict):
        start = time.time()
        raw_path = args.get("path") if isinstance(args, dict) else None
        content = args.get("content") if isinstance(args, dict) else None
        overwrite = args.get("overwrite", True) if isinstance(args, dict) else True

        try:
            path, _ = resolve_session_path(session_id, raw_path)
        except ValueError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        if not isinstance(content, str):
            msg = "`content` must be a string."
            return {"content": [{"type": "text", "text": msg}], "is_error": True}

        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "write_file",
                "input": {"path": path, "bytes": len(content.encode("utf-8"))},
            },
            role="tool",
        )

        try:
            existed = await _file_exists(path)
            if existed and not overwrite:
                msg = (
                    f"File already exists and overwrite=false: {path}. "
                    "Use edit-file to change part of it, or re-call with "
                    "overwrite=true."
                )
                await publish_fn(
                    session_id,
                    "tool_end",
                    {
                        "tool": "write_file",
                        "output": msg,
                        "duration": round(time.time() - start, 1),
                    },
                    role="tool",
                )
                return {"content": [{"type": "text", "text": msg}], "is_error": True}

            await write_to_volume(content, path)
        except Exception as e:
            logger.exception("write_file failed for %s", path)
            err = f"Failed to write {path}: {e}"
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "write_file",
                    "output": err,
                    "duration": round(time.time() - start, 1),
                },
                role="tool",
            )
            return {"content": [{"type": "text", "text": err}], "is_error": True}

        name = path.rsplit("/", 1)[-1]
        event = "file_updated" if existed else "file_created"
        _known_files.setdefault(session_id, set()).add(path)
        await publish_fn(
            session_id,
            event,
            {"path": path, "name": name, "type": "file", "stage": stage},
        )

        result = {
            "ok": True,
            "path": path,
            "bytes_written": len(content.encode("utf-8")),
            "created": not existed,
        }
        await publish_fn(
            session_id,
            "tool_end",
            {
                "tool": "write_file",
                "output": f"{'Updated' if existed else 'Created'} {path} "
                f"({result['bytes_written']} bytes)",
                "duration": round(time.time() - start, 1),
            },
            role="tool",
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    return handler
