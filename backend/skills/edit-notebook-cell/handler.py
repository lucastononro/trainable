"""edit-notebook-cell skill — replace a cell's source in place.

Wraps notebook_store.edit_cell (outputs preserved, same as the frontend's
PUT path). Emits `notebook.structure.changed` so the UI updates live.
"""

from __future__ import annotations

import json
import logging
import time

from services import notebook_store
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)


def create_handler(session_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        start = time.time()
        notebook_name = notebook_store.sanitize_name(args.get("notebook_name"))
        cell_id = (args.get("cell_id") or "").strip()
        source = args.get("source")
        cell_type = (args.get("cell_type") or "").strip() or None

        preview = source if isinstance(source, str) else ""
        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "edit_notebook_cell",
                "input": {
                    "code": preview[:500],
                    "notebook_name": notebook_name,
                    "cell_id": cell_id,
                },
            },
            role="tool",
        )

        async def _fail(msg: str):
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "edit_notebook_cell",
                    "output": msg,
                    "duration": round(time.time() - start, 1),
                },
                role="tool",
            )
            return {"content": [{"type": "text", "text": msg}], "is_error": True}

        if not cell_id:
            return await _fail("`cell_id` must be a non-empty string.")
        if not isinstance(source, str):
            return await _fail("`source` must be a string.")

        try:
            info = await notebook_store.edit_cell(
                session_id, notebook_name, cell_id, source, cell_type=cell_type
            )
        except ValueError as e:
            return await _fail(str(e))
        except Exception as e:
            logger.exception("edit_notebook_cell failed")
            return await _fail(f"Failed to edit cell {cell_id}: {e}")

        try:
            await broadcaster.publish(
                session_id,
                {
                    "type": "notebook.structure.changed",
                    "data": {
                        "reason": "agent_edit",
                        "notebook_name": notebook_name,
                        "notebook_path": info["notebook_path"],
                        "cell_id": cell_id,
                        "total_cells": info["total_cells"],
                    },
                },
            )
        except Exception as e:
            logger.debug("broadcast failed: %s", e)

        summary = (
            f"Edited cell {cell_id} in {notebook_name}.ipynb "
            f"({info['source_len']} chars; outputs preserved — "
            "rerun-notebook-cell to re-execute)"
        )
        await publish_fn(
            session_id,
            "tool_end",
            {
                "tool": "edit_notebook_cell",
                "output": summary,
                "duration": round(time.time() - start, 1),
            },
            role="tool",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "ok": True,
                            "notebook_name": notebook_name,
                            "notebook_path": info["notebook_path"],
                            "cell_id": cell_id,
                            "source_len": info["source_len"],
                        }
                    ),
                }
            ]
        }

    return handler
