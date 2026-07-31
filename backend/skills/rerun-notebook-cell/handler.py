"""rerun-notebook-cell skill — re-execute an existing cell in place.

Looks the cell up by `cell_id`, clears its outputs (via the kernel's
cell_started event, which notebook_store.on_cell_event handles), and
re-executes against the session's persistent kernel — the same kernel
run-notebook-cell uses, so variables from earlier cells stay in scope.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from services import notebook_store
from services.kernel_manager import kernel_manager

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300
_MAX_TIMEOUT = 1800
_MAX_OUTPUT_CHARS_PER_CELL = 4000


def create_handler(session_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        start = time.time()
        notebook_name = notebook_store.sanitize_name(args.get("notebook_name"))
        cell_id = (args.get("cell_id") or "").strip()
        try:
            timeout = int(args.get("timeout_seconds") or _DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = _DEFAULT_TIMEOUT
        timeout = max(10, min(timeout, _MAX_TIMEOUT))

        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "rerun_notebook_cell",
                "input": {"notebook_name": notebook_name, "cell_id": cell_id},
            },
            role="tool",
        )

        async def _fail(msg: str, payload: dict | None = None):
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "rerun_notebook_cell",
                    "output": msg,
                    "duration": round(time.time() - start, 1),
                },
                role="tool",
            )
            text = json.dumps(payload) if payload is not None else msg
            return {"content": [{"type": "text", "text": text}], "is_error": True}

        if not cell_id:
            return await _fail("`cell_id` must be a non-empty string.")

        nb = await notebook_store.load(session_id, notebook_name)
        if nb is None:
            return await _fail(f"Notebook '{notebook_name}' not found.")
        cell = next((c for c in nb.cells if c.get("id") == cell_id), None)
        if cell is None:
            return await _fail(
                f"Cell '{cell_id}' not found in notebook '{notebook_name}'."
            )
        if cell.get("cell_type") != "code":
            return await _fail(
                f"Cell '{cell_id}' is a {cell.get('cell_type')} cell — only "
                "code cells can be re-executed."
            )
        code = notebook_store._src(cell)
        notebook_path = notebook_store.notebook_path(session_id, notebook_name)

        try:
            result = await kernel_manager.execute_and_wait(
                session_id, cell_id, code, notebook_name=notebook_name, timeout=timeout
            )
        except asyncio.TimeoutError:
            err = f"Cell execution exceeded {timeout}s timeout."
            return await _fail(
                err,
                {
                    "ok": False,
                    "notebook_name": notebook_name,
                    "cell_id": cell_id,
                    "error": err,
                },
            )
        except Exception as e:
            logger.exception("rerun_notebook_cell: execute failed")
            return await _fail(f"Failed to execute cell {cell_id}: {e}")

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
            "notebook_path": notebook_path,
            "cell_id": cell_id,
            "exec_count": result.get("exec_count"),
            "duration_ms": result.get("duration_ms"),
            "had_error": result.get("had_error", False),
            "outputs": rendered_outputs,
        }

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
            {
                "tool": "rerun_notebook_cell",
                "output": chat_out,
                "duration": round(time.time() - start, 1),
            },
            role="tool",
        )

        return {"content": [{"type": "text", "text": json.dumps(envelope)}]}

    return handler
