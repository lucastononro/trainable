"""run_notebook_cell — append a code cell to a named notebook AND run it."""

from __future__ import annotations

import asyncio
import json
import logging

from services import notebook_store
from services.broadcaster import broadcaster
from services.kernel_manager import kernel_manager

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300
_MAX_TIMEOUT = 1800
_MAX_OUTPUT_CHARS_PER_CELL = 4000


def create_handler(session_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        code = args.get("code")
        notebook_name = notebook_store.sanitize_name(args.get("notebook_name"))
        after_cell_id = (args.get("after_cell_id") or "").strip() or None
        try:
            timeout = int(args.get("timeout_seconds") or _DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = _DEFAULT_TIMEOUT
        timeout = max(10, min(timeout, _MAX_TIMEOUT))

        # Publish a tool_start so the chat UI shows a collapsible card
        # (`meta.code` drives the code preview — same treatment as execute_code).
        preview = code if isinstance(code, str) else ""
        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "run_notebook_cell",
                "input": {
                    "code": preview[:500],
                    "notebook_name": notebook_name,
                },
            },
            role="tool",
        )

        if not isinstance(code, str) or not code.strip():
            msg = "`code` must be a non-empty string."
            await publish_fn(
                session_id,
                "tool_end",
                {"tool": "run_notebook_cell", "output": msg},
                role="tool",
            )
            return {
                "content": [{"type": "text", "text": msg}],
                "is_error": True,
            }

        try:
            info = await notebook_store.append_cell(
                session_id, notebook_name, "code", code, after_cell_id=after_cell_id
            )
        except Exception as e:
            logger.exception("run_notebook_cell: append failed")
            err = f"Failed to append cell: {e}"
            await publish_fn(
                session_id,
                "tool_end",
                {"tool": "run_notebook_cell", "output": err},
                role="tool",
            )
            return {
                "content": [{"type": "text", "text": err}],
                "is_error": True,
            }
        cell_id = info["cell_id"]

        try:
            if info["created_notebook"]:
                # File-tree announcement so the `.ipynb` lands in the
                # workspace panel the moment the agent creates it.
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
                        "cell_id": cell_id,
                        "total_cells": info["total_cells"],
                    },
                },
            )
        except Exception as e:
            logger.debug("broadcast failed: %s", e)

        try:
            result = await kernel_manager.execute_and_wait(
                session_id, cell_id, code, notebook_name=notebook_name, timeout=timeout
            )
        except asyncio.TimeoutError:
            err = f"Cell execution exceeded {timeout}s timeout."
            await publish_fn(
                session_id,
                "tool_end",
                {"tool": "run_notebook_cell", "output": err},
                role="tool",
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "ok": False,
                                "notebook_name": notebook_name,
                                "cell_id": cell_id,
                                "error": err,
                            }
                        ),
                    }
                ],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("run_notebook_cell: execute failed")
            err = f"Failed to execute cell {cell_id}: {e}"
            await publish_fn(
                session_id,
                "tool_end",
                {"tool": "run_notebook_cell", "output": err},
                role="tool",
            )
            return {
                "content": [{"type": "text", "text": err}],
                "is_error": True,
            }

        rendered_outputs = ""
        nb = await notebook_store.load(session_id, notebook_name)
        if nb is not None:
            cell = next((c for c in nb.cells if c.get("id") == cell_id), None)
            if cell is not None:
                outs = notebook_store._format_outputs(cell.get("outputs", []))
                if len(outs) > _MAX_OUTPUT_CHARS_PER_CELL:
                    outs = outs[:_MAX_OUTPUT_CHARS_PER_CELL] + "\n… (outputs truncated)"
                rendered_outputs = outs

        envelope = {
            "ok": not result.get("had_error", False),
            "notebook_name": notebook_name,
            "notebook_path": info["notebook_path"],
            "cell_id": cell_id,
            "exec_count": result.get("exec_count"),
            "duration_ms": result.get("duration_ms"),
            "had_error": result.get("had_error", False),
            "outputs": rendered_outputs,
        }

        # Output shown in the chat card: compact status + notebook name +
        # outputs preview. The agent still gets the full JSON via the return.
        status = "✗ error" if envelope["had_error"] else "✓ ok"
        preview = rendered_outputs if rendered_outputs else "(no output)"
        if len(preview) > 500:
            preview = preview[:500] + "\n… (truncated)"
        chat_out = (
            f"{status} · {notebook_name}.ipynb "
            f"[{envelope.get('exec_count')}]"
            f" · {envelope.get('duration_ms') or 0} ms\n\n{preview}"
        )
        await publish_fn(
            session_id,
            "tool_end",
            {"tool": "run_notebook_cell", "output": chat_out},
            role="tool",
        )

        return {"content": [{"type": "text", "text": json.dumps(envelope)}]}

    return handler
