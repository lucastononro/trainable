"""list-project-datasets handler — surfaces every DatasetVersion in the
project the current session belongs to so the agent can pick a sensible
parent_dataset_id when calling register-dataset.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from db import async_session
from models import Experiment, Session as SessionModel
from services.lineage import list_project_datasets

logger = logging.getLogger(__name__)


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(_args: dict):
        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "list-project-datasets",
                    "input": {},
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict

        if not session_id:
            output_text = "list-project-datasets failed: requires an active session_id"
            is_error = True
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": "list-project-datasets requires an active session_id.",
                    }
                ],
                "is_error": True,
            }
        else:
            project_id = None
            session_missing = False
            async with async_session() as db:
                sess = (
                    await db.execute(
                        select(SessionModel).where(SessionModel.id == session_id)
                    )
                ).scalar_one_or_none()
                if not sess:
                    session_missing = True
                else:
                    project_id = sess.project_id
                    if not project_id and sess.experiment_id:
                        project_id = (
                            await db.execute(
                                select(Experiment.project_id).where(
                                    Experiment.id == sess.experiment_id
                                )
                            )
                        ).scalar_one_or_none()

            if session_missing:
                output_text = (
                    f"list-project-datasets failed: session {session_id} not found"
                )
                is_error = True
                response = {
                    "content": [
                        {"type": "text", "text": f"Session {session_id} not found."}
                    ],
                    "is_error": True,
                }
            elif not project_id:
                output_text = "list-project-datasets failed: session has no project_id"
                is_error = True
                response = {
                    "content": [
                        {
                            "type": "text",
                            "text": "Session has no project_id; cannot list datasets.",
                        }
                    ],
                    "is_error": True,
                }
            else:
                rows = await list_project_datasets(project_id)
                compact = [
                    {
                        "id": r["id"],
                        "kind": r["kind"],
                        "name": r["name"],
                        "description": r["description"],
                        "hash": (r["hash"] or "")[:12] + "…" if r["hash"] else "",
                        "parent_id": r["parent_id"],
                        "created_at": r["created_at"],
                    }
                    for r in rows
                ]
                output_text = f"Listed {len(compact)} dataset(s)"
                response = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"datasets": compact}, indent=2, default=str
                            ),
                        }
                    ]
                }

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "list-project-datasets",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
