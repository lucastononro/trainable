"""list_session_files tool — list files in another session's workspace.

Given a session_id that belongs to the same project, returns the files under
its /sessions/{sid}/ workspace on the Modal Volume. Useful for an agent that
wants to pull a report, script, or image produced in a sibling chat.

Safeguards:
- Rejects cross-project reads (same pattern as read_project_session).
- Caps the response so large workspaces don't blow the tool result budget.
"""

from __future__ import annotations

import fnmatch
import json
import logging

from sqlalchemy import select

from db import async_session
from models import Experiment
from models import Session as SessionModel
from services.volume import get_volume, reload_volume

logger = logging.getLogger(__name__)

_MAX_FILES = 200
_MAX_RESPONSE_CHARS = 8000


def create_handler(session_id: str, experiment_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        target_session_id = (args.get("session_id") or "").strip()
        if not target_session_id:
            return {
                "content": [{"type": "text", "text": "session_id is required"}],
                "is_error": True,
            }

        pattern = (args.get("glob") or "").strip() or None
        try:
            limit = min(int(args.get("limit") or _MAX_FILES), _MAX_FILES)
        except (TypeError, ValueError):
            limit = _MAX_FILES

        try:
            async with async_session() as db:
                cur_exp_row = await db.execute(
                    select(Experiment).where(Experiment.id == experiment_id)
                )
                cur_exp = cur_exp_row.scalar_one_or_none()
                if not cur_exp or not cur_exp.project_id:
                    return {
                        "content": [{
                            "type": "text",
                            "text": "Current experiment has no project — cannot cross-reference sessions.",
                        }],
                        "is_error": True,
                    }
                project_id = cur_exp.project_id

                target_row = await db.execute(
                    select(SessionModel).where(SessionModel.id == target_session_id)
                )
                target_session = target_row.scalar_one_or_none()
                if not target_session:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Session {target_session_id} not found.",
                        }],
                        "is_error": True,
                    }

                target_exp_row = await db.execute(
                    select(Experiment).where(Experiment.id == target_session.experiment_id)
                )
                target_exp = target_exp_row.scalar_one_or_none()
                if not target_exp or target_exp.project_id != project_id:
                    return {
                        "content": [{
                            "type": "text",
                            "text": (
                                f"Session {target_session_id} does not belong to the "
                                f"current project. Cross-project reads are not allowed."
                            ),
                        }],
                        "is_error": True,
                    }
        except Exception as e:
            logger.exception("list_session_files db check failed")
            return {
                "content": [{"type": "text", "text": f"DB error: {e}"}],
                "is_error": True,
            }

        workspace = f"/sessions/{target_session_id}"
        try:
            reload_volume()
            vol = get_volume()
            entries = list(vol.listdir(workspace, recursive=True))
        except Exception as e:
            logger.info("list_session_files empty or missing workspace %s: %s", workspace, e)
            entries = []

        files = []
        truncated = False
        for entry in entries:
            if entry.type.name != "FILE":
                continue
            path = entry.path
            rel = path[len(workspace) + 1:] if path.startswith(workspace + "/") else path
            if pattern and not fnmatch.fnmatch(rel, pattern):
                continue
            size = getattr(entry, "size", None)
            mtime = getattr(entry, "mtime", None)
            files.append({"path": path, "rel": rel, "size": size, "mtime": mtime})
            if len(files) >= limit:
                truncated = True
                break

        files.sort(key=lambda f: f["rel"])

        envelope = {
            "session_id": target_session_id,
            "chat_name": target_exp.name,
            "project_id": project_id,
            "workspace": workspace,
            "file_count": len(files),
            "truncated": truncated,
            "files": files,
        }
        payload = json.dumps(envelope, default=str, indent=2)
        if len(payload) > _MAX_RESPONSE_CHARS:
            payload = payload[:_MAX_RESPONSE_CHARS] + f"\n…[capped at {_MAX_RESPONSE_CHARS} chars]"

        return {"content": [{"type": "text", "text": payload}]}

    return handler
