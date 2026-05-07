"""register-model handler — agent declares the trained artifact and
closes the training window.
"""

from __future__ import annotations

import json
import logging

from services.registry import register_model_declared

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        top_metric = ""
        metrics_arg = args.get("metrics")
        if metrics_arg and isinstance(metrics_arg, dict):
            items = list(metrics_arg.items())
            if items:
                k, v = items[0]
                try:
                    top_metric = f"{k}={float(v):.3f}"
                except Exception:
                    top_metric = f"{k}={v}"

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "register-model",
                    "input": {
                        "experiment_id": (args.get("experiment_id") or "")[:8] + "…",
                        "name": args.get("name") or "(experiment default)",
                        "framework": args.get("framework") or "(none)",
                        "top_metric": top_metric or "—",
                    },
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict

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
            summary = {
                "model_id": row["id"],
                "experiment_id": row["experiment_id"],
                "name": row["name"],
                "version": row["version"],
                "artifact_uri": row["artifact_uri"],
                "metrics": row["metrics_summary"],
            }
            output_text = (
                f"Registered {row['name']} v{row['version']} "
                f"({row['framework']}); experiment → trained"
            )
            response = {
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
        except ValueError as e:
            output_text = f"register-model failed: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"register-model failed: {e}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("register-model unexpected failure")
            output_text = f"register-model error: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"register-model error: {e}"}],
                "is_error": True,
            }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "register-model",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
