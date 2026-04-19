"""append_notebook_cell — agents drop cells into a named notebook.

Each session can have many notebooks under
`/data/sessions/{session_id}/notebooks/{name}.ipynb`. This tool appends
(or inserts) a single cell into one of them, creating the notebook if
it doesn't exist. Emits a `notebook.structure.changed` SSE event and a
`notebook.created` event the first time a given notebook shows up.
"""

from __future__ import annotations

import json
import logging

from services import notebook_store
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)


def create_handler(session_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        cell_type = (args.get("cell_type") or "").strip()
        source = args.get("source")
        notebook_name = notebook_store.sanitize_name(args.get("notebook_name"))
        after_cell_id = (args.get("after_cell_id") or "").strip() or None

        # Publish a tool_start event so the chat UI renders a collapsible card
        # (same treatment as execute_code). `code` goes into meta.code, the
        # rest are rendered alongside for context.
        preview_code = source if isinstance(source, str) else ""
        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "append_notebook_cell",
                "input": {
                    "code": preview_code[:500],
                    "notebook_name": notebook_name,
                    "cell_type": cell_type,
                },
            },
            role="tool",
        )

        if cell_type not in ("code", "markdown"):
            msg = "cell_type must be 'code' or 'markdown'."
            await publish_fn(
                session_id, "tool_end",
                {"tool": "append_notebook_cell", "output": msg},
                role="tool",
            )
            return {
                "content": [{"type": "text", "text": msg}],
                "is_error": True,
            }
        if not isinstance(source, str):
            msg = "source must be a string."
            await publish_fn(
                session_id, "tool_end",
                {"tool": "append_notebook_cell", "output": msg},
                role="tool",
            )
            return {
                "content": [{"type": "text", "text": msg}],
                "is_error": True,
            }

        try:
            info = await notebook_store.append_cell(
                session_id,
                notebook_name,
                cell_type,
                source,
                after_cell_id=after_cell_id,
            )
        except Exception as e:
            logger.exception("append_notebook_cell failed")
            err = f"Failed to append cell: {e}"
            await publish_fn(
                session_id, "tool_end",
                {"tool": "append_notebook_cell", "output": err},
                role="tool",
            )
            return {
                "content": [{"type": "text", "text": err}],
                "is_error": True,
            }

        try:
            if info["created_notebook"]:
                # Tell the file tree so the .ipynb appears in the workspace
                # panel as soon as the agent creates it (matches how
                # execute_code announces new scripts).
                await broadcaster.publish(
                    session_id,
                    {
                        "type": "file_created",
                        "data": {
                            "name": f"{notebook_name}.ipynb",
                            "path": info["notebook_path"],
                            "type": "file",
                            "stage": "notebooks",
                        },
                    },
                )
                await broadcaster.publish(
                    session_id,
                    {
                        "type": "notebook.created",
                        "data": {
                            "notebook_name": notebook_name,
                            "notebook_path": info["notebook_path"],
                        },
                    },
                )
            await broadcaster.publish(
                session_id,
                {
                    "type": "notebook.structure.changed",
                    "data": {
                        "reason": "agent_append",
                        "notebook_name": notebook_name,
                        "notebook_path": info["notebook_path"],
                        "cell_id": info["cell_id"],
                        "total_cells": info["total_cells"],
                    },
                },
            )
        except Exception as e:
            logger.debug("broadcast failed: %s", e)

        summary = (
            f"Appended {cell_type} cell to {notebook_name}.ipynb "
            f"({info['total_cells']} cells total"
            f"{'; new notebook' if info['created_notebook'] else ''})"
        )
        await publish_fn(
            session_id, "tool_end",
            {"tool": "append_notebook_cell", "output": summary},
            role="tool",
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "ok": True,
                    "notebook_name": notebook_name,
                    "notebook_path": info["notebook_path"],
                    "cell_id": info["cell_id"],
                    "cell_type": info["cell_type"],
                    "total_cells": info["total_cells"],
                    "created_notebook": info["created_notebook"],
                }),
            }]
        }

    return handler
