"""start-training handler — transitions an experiment to TRAINING and
freezes the hyperparams the agent intends to use.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from db import async_session
from models import Experiment, ExperimentState
from services.experiments import transition_state

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        eid = str(args.get("experiment_id") or "").strip()
        framework = str(args.get("framework") or "").strip()
        hyperparams = args.get("hyperparams") or {}

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "start-training",
                    "input": {
                        "experiment_id": (args.get("experiment_id") or "")[:8] + "…",
                        "framework": args.get("framework") or "(none)",
                    },
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict

        if not eid or not framework:
            output_text = (
                "start-training failed: experiment_id and framework are required"
            )
            is_error = True
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": "experiment_id and framework are required",
                    }
                ],
                "is_error": True,
            }
        else:
            try:
                # Stash framework + hyperparams on the experiment row before the
                # state transition so they're visible the moment the lineage view
                # refreshes on `experiment_state_changed`.
                async with async_session() as db:
                    exp = (
                        await db.execute(select(Experiment).where(Experiment.id == eid))
                    ).scalar_one_or_none()
                    if not exp:
                        output_text = (
                            f"start-training failed: Experiment {eid} not found"
                        )
                        is_error = True
                        response = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Experiment {eid} not found",
                                }
                            ],
                            "is_error": True,
                        }
                    else:
                        # Hyperparams + framework live on the eventual RegisteredModel
                        # row, but we stash a snapshot in description until then so the
                        # state-change SSE carries useful context. Keep this minimal.
                        if framework and not (exp.description or "").startswith(
                            "framework="
                        ):
                            exp.description = (
                                f"framework={framework}; "
                                f"hyperparams={json.dumps(hyperparams)[:300]}"
                            )
                        await db.commit()

                if not is_error:
                    row = await transition_state(
                        experiment_id=eid,
                        new_state=ExperimentState.TRAINING.value,
                        started_at=datetime.now(timezone.utc).isoformat(),
                    )
                    summary = {
                        "experiment_id": row["id"],
                        "state": row["state"],
                        "started_at": row["started_at"],
                        "framework": framework,
                    }
                    output_text = (
                        f"Training started for experiment {row['id'][:8]}… "
                        f"framework={framework}"
                    )
                    response = {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Training started. Call register-model immediately "
                                    "after .fit() completes — otherwise this experiment "
                                    "will be marked abandoned.\n\n"
                                    + json.dumps(summary, indent=2)
                                ),
                            }
                        ]
                    }
            except ValueError as e:
                output_text = f"start-training failed: {e}"
                is_error = True
                response = {
                    "content": [
                        {"type": "text", "text": f"start-training failed: {e}"}
                    ],
                    "is_error": True,
                }
            except Exception as e:
                logger.exception("start-training unexpected failure")
                output_text = f"start-training error: {e}"
                is_error = True
                response = {
                    "content": [{"type": "text", "text": f"start-training error: {e}"}],
                    "is_error": True,
                }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "start-training",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
