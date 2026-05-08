"""create-serving-app handler — generate a Modal serving app for a
registered model.
"""

from __future__ import annotations

import json
import logging

from services.deploy import generate_serving_app

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        model_id = str(args.get("model_id") or "").strip()
        compute = str(args.get("compute") or "cpu").strip() or "cpu"

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "create-serving-app",
                    "input": {
                        "model_id": (model_id or "")[:8] + "…",
                        "compute": compute,
                    },
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict
        try:
            if not model_id:
                raise ValueError("model_id is required")
            out = await generate_serving_app(model_id, compute=compute)
            output_text = (
                f"Serving app written: {out['serving_app_path']}. "
                f"Deploy button on /models is now enabled for this model."
            )
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Modal serving app generated. The Deploy button on "
                            "/models is now active for this model — clicking it "
                            "will run `modal deploy` and return the real "
                            "endpoint URL.\n\n"
                            + json.dumps(
                                {k: v for k, v in out.items() if k != "code_preview"},
                                indent=2,
                            )
                            + "\n\nFirst lines of generated app:\n\n"
                            + out["code_preview"]
                        ),
                    }
                ]
            }
        except ValueError as e:
            output_text = f"create-serving-app failed: {e}"
            is_error = True
            response = {
                "content": [
                    {"type": "text", "text": f"create-serving-app failed: {e}"}
                ],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("create-serving-app unexpected failure")
            output_text = f"create-serving-app error: {e}"
            is_error = True
            response = {
                "content": [
                    {"type": "text", "text": f"create-serving-app error: {e}"}
                ],
                "is_error": True,
            }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "create-serving-app",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
