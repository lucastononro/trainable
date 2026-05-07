"""create-experiment handler.

The agent calls this *before* any prep/training so the platform has a
registration unit to attach datasets and models to.
"""

from __future__ import annotations

import json
import logging

from services.experiments import create_experiment_declared

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        # tool_start — compact summary of inputs for the chat card.
        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "create-experiment",
                    "input": {
                        "name": args.get("name") or "(no name)",
                        "hypothesis": (args.get("hypothesis") or "")[:120],
                    },
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict

        if not session_id:
            output_text = (
                "create-experiment failed: requires an active session_id "
                "in the agent's runtime context."
            )
            is_error = True
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "create-experiment requires an active session_id "
                            "in the agent's runtime context."
                        ),
                    }
                ],
                "is_error": True,
            }
        else:
            try:
                row = await create_experiment_declared(
                    session_id=session_id,
                    name=str(args.get("name") or "").strip(),
                    hypothesis=str(args.get("hypothesis") or "").strip(),
                    description=str(args.get("description") or ""),
                    parent_dataset_ids=args.get("parent_dataset_ids") or None,
                )
                summary = {
                    "experiment_id": row["id"],
                    "session_id": row["session_id"],
                    "name": row["name"],
                    "state": row["state"],
                    "hypothesis": row["hypothesis"],
                }
                output_text = (
                    f"Created experiment {row['id'][:8]}… "
                    f"({row['name']}) state={row['state']}"
                )
                response = {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Experiment created. Save this experiment_id and pass "
                                "it to register-dataset, start-training, and "
                                "register-model.\n\n" + json.dumps(summary, indent=2)
                            ),
                        }
                    ]
                }
            except ValueError as e:
                output_text = f"create-experiment failed: {e}"
                is_error = True
                response = {
                    "content": [
                        {"type": "text", "text": f"create-experiment failed: {e}"}
                    ],
                    "is_error": True,
                }
            except Exception as e:
                logger.exception("create-experiment unexpected failure")
                output_text = f"create-experiment error: {e}"
                is_error = True
                response = {
                    "content": [
                        {"type": "text", "text": f"create-experiment error: {e}"}
                    ],
                    "is_error": True,
                }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "create-experiment",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
