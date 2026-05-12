"""fork-experiment handler — derive a new experiment from an existing
parent's dataset linkages.
"""

from __future__ import annotations

import json
import logging

from services.experiments import fork_experiment

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        parent_id = str(args.get("parent_experiment_id") or "").strip()
        name = str(args.get("name") or "").strip()
        hypothesis = str(args.get("hypothesis") or "").strip()
        description = str(args.get("description") or "")

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "fork-experiment",
                    "input": {
                        "parent_experiment_id": parent_id[:8] + "…"
                        if parent_id
                        else "(missing)",
                        "name": name or "(no name)",
                        "hypothesis": hypothesis[:120],
                    },
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict
        try:
            row = await fork_experiment(
                parent_experiment_id=parent_id,
                name=name,
                hypothesis=hypothesis,
                description=description,
            )
            summary = {
                "experiment_id": row["id"],
                "parent_experiment_id": parent_id,
                "session_id": row["session_id"],
                "name": row["name"],
                "state": row["state"],
            }
            output_text = (
                f"Forked → new experiment {row['id'][:8]}… ({row['name']}) "
                f"state={row['state']}"
            )
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Forked experiment created. Inherits the parent's "
                            "input datasets — call start-training next.\n\n"
                            + json.dumps(summary, indent=2)
                        ),
                    }
                ]
            }
        except ValueError as e:
            output_text = f"fork-experiment failed: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"fork-experiment failed: {e}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("fork-experiment unexpected failure")
            output_text = f"fork-experiment error: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"fork-experiment error: {e}"}],
                "is_error": True,
            }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "fork-experiment",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
