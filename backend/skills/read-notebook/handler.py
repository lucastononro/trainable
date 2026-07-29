"""read_notebook — read a named notebook (or list notebooks) for the agent."""

from __future__ import annotations

import logging

from services import notebook_store

logger = logging.getLogger(__name__)


def create_handler(session_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        notebook_name = args.get("notebook_name")
        include_outputs = bool(args.get("include_outputs", True))
        try:
            max_chars = int(args.get("max_chars_per_cell") or 4000)
        except (TypeError, ValueError):
            max_chars = 4000
        max_chars = max(200, min(max_chars, 20000))

        # Publish a tool_start so the chat UI shows a collapsible card.
        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "read_notebook",
                "input": {
                    "notebook_name": notebook_name or "(list)",
                },
            },
            role="tool",
        )

        if not notebook_name:
            try:
                items = await notebook_store.list_notebooks(session_id)
            except Exception as e:
                logger.exception("read_notebook: list failed")
                err = f"Failed to list notebooks: {e}"
                await publish_fn(
                    session_id,
                    "tool_end",
                    {"tool": "read_notebook", "output": err},
                    role="tool",
                )
                return {
                    "content": [{"type": "text", "text": err}],
                    "is_error": True,
                }
            if not items:
                msg = (
                    "No notebooks exist in this session yet. Call "
                    "`run_notebook_cell(notebook_name='my-analysis', code=...)` "
                    "to create one."
                )
                await publish_fn(
                    session_id,
                    "tool_end",
                    {"tool": "read_notebook", "output": "Listed 0 notebooks"},
                    role="tool",
                )
                return {"content": [{"type": "text", "text": msg}]}
            summary = "Available notebooks in this session:\n" + "\n".join(
                f"- `{it['name']}` ({it.get('cells') or '?'} cells) → {it['path']}"
                for it in items
            )
            summary += "\n\nCall `read_notebook(notebook_name='<name>')` to read a specific one."
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "read_notebook",
                    "output": f"Listed {len(items)} notebook(s): "
                    + ", ".join(it["name"] for it in items),
                },
                role="tool",
            )
            return {"content": [{"type": "text", "text": summary}]}

        notebook_name = notebook_store.sanitize_name(notebook_name)
        try:
            rendered = await notebook_store.render_for_agent(
                session_id,
                notebook_name,
                include_outputs=include_outputs,
                max_chars_per_cell=max_chars,
            )
        except Exception as e:
            logger.exception("read_notebook failed")
            err = f"Failed to read notebook: {e}"
            await publish_fn(
                session_id,
                "tool_end",
                {"tool": "read_notebook", "output": err},
                role="tool",
            )
            return {
                "content": [{"type": "text", "text": err}],
                "is_error": True,
            }

        if rendered is None:
            msg = (
                f"Notebook `{notebook_name}` does not exist. "
                "Use `read_notebook()` (no args) to list notebooks, or "
                "`run_notebook_cell(notebook_name=..., code=...)` to create."
            )
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "read_notebook",
                    "output": f"Notebook `{notebook_name}` not found.",
                },
                role="tool",
            )
            return {"content": [{"type": "text", "text": msg}]}

        # Summary for the chat card; agent still gets full render.
        first_line = rendered.splitlines()[0] if rendered else ""
        await publish_fn(
            session_id,
            "tool_end",
            {"tool": "read_notebook", "output": first_line},
            role="tool",
        )
        return {"content": [{"type": "text", "text": rendered}]}

    return handler
