"""delete-notebook-cell skill — remove a cell from a notebook.

Wraps notebook_store.delete_cell. Emits `notebook.structure.changed` so
the UI updates live.
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

        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "delete_notebook_cell",
                "input": {"notebook_name": notebook_name, "cell_id": cell_id},
            },
            role="tool",
        )

        async def _fail(msg: str):
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "delete_notebook_cell",
                    "output": msg,
                    "duration": round(time.time() - start, 1),
                },
                role="tool",
            )
            return {"content": [{"type": "text", "text": msg}], "is_error": True}

        if not cell_id:
            return await _fail("`cell_id` must be a non-empty string.")

        try:
            info = await notebook_store.delete_cell(session_id, notebook_name, cell_id)
        except ValueError as e:
            return await _fail(str(e))
        except Exception as e:
            logger.exception("delete_notebook_cell failed")
            return await _fail(f"Failed to delete cell {cell_id}: {e}")

        try:
            await broadcaster.publish(
                session_id,
                {
                    "type": "notebook.structure.changed",
                    "data": {
                        "reason": "agent_delete",
                        "notebook_name": notebook_name,
                        "notebook_path": info["notebook_path"],
                        "cell_id": cell_id,
                        "total_cells": info["remaining_cells"],
                    },
                },
            )
        except Exception as e:
            logger.debug("broadcast failed: %s", e)

        summary = (
            f"Deleted cell {cell_id} from {notebook_name}.ipynb "
            f"({info['remaining_cells']} cells remaining)"
        )
        await publish_fn(
            session_id,
            "tool_end",
            {
                "tool": "delete_notebook_cell",
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
                            "remaining_cells": info["remaining_cells"],
                        }
                    ),
                }
            ]
        }

    return handler
