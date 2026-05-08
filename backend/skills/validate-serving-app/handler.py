"""validate-serving-app handler — pre-deploy sanity check on a model's
Modal serving app.
"""

from __future__ import annotations

import json
import logging

from services.deploy import validate_serving_app

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        model_id = str(args.get("model_id") or "").strip()

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "validate-serving-app",
                    "input": {"model_id": (model_id or "")[:8] + "…"},
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict
        try:
            if not model_id:
                raise ValueError("model_id is required")
            out = await validate_serving_app(model_id)
            ok = bool(out.get("ok"))
            issues = out.get("issues") or []
            warnings = out.get("warnings") or []
            output_text = (
                f"validate: ok={ok}, {len(issues)} issue(s), {len(warnings)} warning(s)"
            )
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            ("✅ Validation passed. Safe to deploy.\n\n"
                             if ok
                             else "❌ Validation failed. Fix the issues below before deploy.\n\n")
                            + json.dumps(
                                {
                                    "ok": ok,
                                    "issues": issues,
                                    "warnings": warnings,
                                    "artifact_path_in_app": out.get(
                                        "artifact_path_in_app"
                                    ),
                                    "pip_packages": out.get("pip_packages"),
                                },
                                indent=2,
                            )
                        ),
                    }
                ],
                "is_error": not ok,
            }
            is_error = not ok
        except ValueError as e:
            output_text = f"validate-serving-app failed: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"validate-serving-app failed: {e}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("validate-serving-app unexpected failure")
            output_text = f"validate-serving-app error: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"validate-serving-app error: {e}"}],
                "is_error": True,
            }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "validate-serving-app",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
