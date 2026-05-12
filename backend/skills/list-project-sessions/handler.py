"""list_project_sessions tool — enumerate all chats/sessions in the current project.

Returns a JSON list of every session under the agent's current project, with
enough metadata for the agent to decide which ones are worth inspecting.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import func, select

from db import async_session
from models import Experiment, Message
from models import Session as SessionModel

logger = logging.getLogger(__name__)


def create_handler(session_id: str, experiment_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        try:
            async with async_session() as db:
                # Find the current project via the experiment.
                exp_row = await db.execute(
                    select(Experiment).where(Experiment.id == experiment_id)
                )
                current_exp = exp_row.scalar_one_or_none()
                if not current_exp or not current_exp.project_id:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "Current experiment has no project — cannot list project sessions.",
                            }
                        ],
                        "is_error": True,
                    }
                project_id = current_exp.project_id

                # Fetch all experiments (chats) in this project.
                exps_result = await db.execute(
                    select(Experiment).where(Experiment.project_id == project_id)
                )
                experiments = list(exps_result.scalars().all())
                if not experiments:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "No sessions found in this project.",
                            }
                        ]
                    }

                exp_by_id = {e.id: e for e in experiments}
                exp_ids = list(exp_by_id.keys())

                # Fetch all sessions belonging to those experiments.
                sessions_result = await db.execute(
                    select(SessionModel).where(SessionModel.experiment_id.in_(exp_ids))
                )
                sessions = list(sessions_result.scalars().all())

                # Count messages per session in one query.
                counts_result = await db.execute(
                    select(Message.session_id, func.count(Message.id))
                    .where(Message.session_id.in_([s.id for s in sessions]))
                    .group_by(Message.session_id)
                )
                counts = {sid: count for sid, count in counts_result.all()}

            items = []
            for s in sorted(
                sessions, key=lambda x: x.updated_at or x.created_at or "", reverse=True
            ):
                exp = exp_by_id.get(s.experiment_id)
                items.append(
                    {
                        "session_id": s.id,
                        "experiment_id": s.experiment_id,
                        "chat_name": exp.name if exp else None,
                        "state": s.state,
                        "created_at": s.created_at,
                        "updated_at": s.updated_at,
                        "message_count": counts.get(s.id, 0),
                        "is_current": s.id == session_id,
                    }
                )

            envelope = {
                "project_id": project_id,
                "session_count": len(items),
                "sessions": items,
            }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(envelope, default=str, indent=2),
                    }
                ]
            }
        except Exception as e:
            logger.exception("list_project_sessions failed")
            return {
                "content": [{"type": "text", "text": f"DB error: {e}"}],
                "is_error": True,
            }

    return handler
