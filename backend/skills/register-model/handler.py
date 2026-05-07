"""register-model handler — agent declares the trained artifact and
closes the training window.
"""

from __future__ import annotations

import json
import logging

from services.registry import register_model_declared

logger = logging.getLogger(__name__)


def create_handler(**_):
    async def handler(args: dict):
        try:
            row = await register_model_declared(
                experiment_id=str(args.get("experiment_id") or "").strip(),
                path=str(args.get("path") or "").strip(),
                framework=str(args.get("framework") or "").strip(),
                metrics=args.get("metrics") or {},
                description=str(args.get("description") or "").strip(),
                hyperparams=args.get("hyperparams") or None,
                name=args.get("name"),
            )
        except ValueError as e:
            return {
                "content": [{"type": "text", "text": f"register-model failed: {e}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("register-model unexpected failure")
            return {
                "content": [{"type": "text", "text": f"register-model error: {e}"}],
                "is_error": True,
            }

        summary = {
            "model_id": row["id"],
            "experiment_id": row["experiment_id"],
            "name": row["name"],
            "version": row["version"],
            "artifact_uri": row["artifact_uri"],
            "metrics": row["metrics_summary"],
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Model registered, experiment marked as trained, "
                        "snapshot captured. The lineage canvas should now "
                        "show this model connected to its experiment + dataset.\n\n"
                        + json.dumps(summary, indent=2)
                    ),
                }
            ]
        }

    return handler
