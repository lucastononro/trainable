"""create-experiment handler.

The agent calls this *before* any prep/training so the platform has a
registration unit to attach datasets and models to.
"""

from __future__ import annotations

import json
import logging

from services.experiments import create_experiment_declared

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", **_):
    async def handler(args: dict):
        if not session_id:
            return {
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
        try:
            row = await create_experiment_declared(
                session_id=session_id,
                name=str(args.get("name") or "").strip(),
                hypothesis=str(args.get("hypothesis") or "").strip(),
                description=str(args.get("description") or ""),
                parent_dataset_ids=args.get("parent_dataset_ids") or None,
            )
        except ValueError as e:
            return {
                "content": [{"type": "text", "text": f"create-experiment failed: {e}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("create-experiment unexpected failure")
            return {
                "content": [{"type": "text", "text": f"create-experiment error: {e}"}],
                "is_error": True,
            }
        # Compact summary so the agent doesn't get the full payload back.
        summary = {
            "experiment_id": row["id"],
            "session_id": row["session_id"],
            "name": row["name"],
            "state": row["state"],
            "hypothesis": row["hypothesis"],
        }
        return {
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

    return handler
