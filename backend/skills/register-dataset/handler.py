"""register-dataset handler — agent declares a processed DatasetVersion."""

from __future__ import annotations

import json
import logging

from services.dataset_versions import register_dataset_declared

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "register-dataset",
                    "input": {
                        "experiment_id": (args.get("experiment_id") or "")[:8] + "…",
                        "name": args.get("name") or "(no name)",
                        "role": args.get("role") or "input",
                        "parent_dataset_id": args.get("parent_dataset_id"),
                    },
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict

        try:
            row = await register_dataset_declared(
                experiment_id=str(args.get("experiment_id") or "").strip(),
                path=str(args.get("path") or "").strip(),
                name=str(args.get("name") or "").strip(),
                description=str(args.get("description") or "").strip(),
                role=str(args.get("role") or "input"),
                parent_dataset_id=args.get("parent_dataset_id"),
                metadata=args.get("metadata") or None,
                content_hash=str(args.get("content_hash") or "").strip() or None,
                size_bytes=int(args.get("size_bytes") or 0),
            )
            summary = {
                "dataset_version_id": row["id"],
                "kind": row["kind"],
                "name": row["name"],
                "hash": row["hash"][:12] + "…" if row["hash"] else "",
                "parent_id": row.get("parent_id"),
            }
            output_text = (
                f"Registered {row['kind']} dataset id={row['id']} "
                f"parent_id={row.get('parent_id') or 'none'}"
            )
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Dataset registered. Save dataset_version_id if you "
                            "plan to derive further datasets from it.\n\n"
                            + json.dumps(summary, indent=2)
                        ),
                    }
                ]
            }
        except ValueError as e:
            output_text = f"register-dataset failed: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"register-dataset failed: {e}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("register-dataset unexpected failure")
            output_text = f"register-dataset error: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"register-dataset error: {e}"}],
                "is_error": True,
            }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "register-dataset",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
