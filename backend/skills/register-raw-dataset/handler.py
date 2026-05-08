"""register-raw-dataset handler — declare a file on the volume as a
raw DatasetVersion. Idempotent: re-registering the same bytes returns
the existing row.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from db import async_session
from models import Experiment
from services.dataset_versions import record_upload
from services.volume import read_volume_file_async

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        path = str(args.get("path") or "").strip()
        project_id = (args.get("project_id") or "").strip()
        experiment_id = (args.get("experiment_id") or "").strip()
        name = args.get("name")
        description = str(args.get("description") or "").strip()

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "register-raw-dataset",
                    "input": {
                        "path": path[-60:] if len(path) > 60 else path,
                        "project_id": (project_id or experiment_id)[:8] + "…",
                    },
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict

        try:
            if not path:
                raise ValueError("path is required for register-raw-dataset")
            # Resolve project_id from experiment_id if needed.
            if not project_id:
                if not experiment_id:
                    raise ValueError(
                        "either project_id or experiment_id must be supplied"
                    )
                async with async_session() as db:
                    exp = (
                        await db.execute(
                            select(Experiment).where(Experiment.id == experiment_id)
                        )
                    ).scalar_one_or_none()
                    if not exp:
                        raise ValueError(f"Experiment {experiment_id} not found")
                    project_id = exp.project_id

            # Read the file off the volume so record_upload can hash it.
            data = await read_volume_file_async(path)
            row = await record_upload(
                project_id=project_id,
                path=path,
                content=data,
                name=name,
                description=description,
            )
            output_text = (
                f"Raw dataset registered (or deduped): id={row['id']} "
                f"name={row['name']} ({row['size_bytes']}B)"
            )
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Raw dataset is in the catalog. Pass its id as "
                            "`parent_dataset_id` to register-dataset when you "
                            "declare the processed split.\n\n"
                            + json.dumps(row, indent=2)
                        ),
                    }
                ]
            }
        except FileNotFoundError as e:
            output_text = f"register-raw-dataset failed: file not found at {path}"
            is_error = True
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"register-raw-dataset failed: {e}. "
                            "Confirm the path exists on the volume — "
                            "use list-session-files / list-project-datasets first."
                        ),
                    }
                ],
                "is_error": True,
            }
        except ValueError as e:
            output_text = f"register-raw-dataset failed: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"register-raw-dataset failed: {e}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.exception("register-raw-dataset unexpected failure")
            output_text = f"register-raw-dataset error: {e}"
            is_error = True
            response = {
                "content": [{"type": "text", "text": f"register-raw-dataset error: {e}"}],
                "is_error": True,
            }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "register-raw-dataset",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
