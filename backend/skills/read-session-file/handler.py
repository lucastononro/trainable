"""read_session_file tool — read a single file from another session's workspace.

Complements list_session_files. Text files are returned as decoded UTF-8;
binary files (images, parquet, pickles) are base64-encoded and flagged so
the agent knows to download rather than try to parse inline.

Safeguards:
- Rejects cross-project reads (verifies target session belongs to the same project).
- Path must stay inside /sessions/{target_sid}/ (rejects traversal).
- Text responses capped; binary responses further capped at ~200KB.
"""

from __future__ import annotations

import base64
import json
import logging

from sqlalchemy import select

from db import async_session
from models import Experiment
from models import Session as SessionModel
from services.volume import read_volume_file_async, reload_volume_async

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 12000
_MAX_BINARY_BYTES = 200_000
_TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".log",
    ".sh",
    ".sql",
}


def _is_text_path(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(suf) for suf in _TEXT_SUFFIXES)


def create_handler(session_id: str, experiment_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        target_session_id = (args.get("session_id") or "").strip()
        path = (args.get("path") or "").strip()
        if not target_session_id or not path:
            return {
                "content": [
                    {"type": "text", "text": "session_id and path are required"}
                ],
                "is_error": True,
            }

        try:
            offset = max(int(args.get("offset") or 0), 0)
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = int(args.get("limit") or _MAX_TEXT_CHARS)
        except (TypeError, ValueError):
            limit = _MAX_TEXT_CHARS
        limit = min(limit, _MAX_TEXT_CHARS)

        workspace = f"/sessions/{target_session_id}"
        normalized = path if path.startswith("/") else f"{workspace}/{path}"
        if not normalized.startswith(workspace + "/") and normalized != workspace:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Path must be inside {workspace}/. Got: {path}",
                    }
                ],
                "is_error": True,
            }
        if ".." in normalized.split("/"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Path traversal ('..') is not allowed.",
                    }
                ],
                "is_error": True,
            }

        try:
            async with async_session() as db:
                cur_exp_row = await db.execute(
                    select(Experiment).where(Experiment.id == experiment_id)
                )
                cur_exp = cur_exp_row.scalar_one_or_none()
                if not cur_exp or not cur_exp.project_id:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "Current experiment has no project — cannot cross-reference sessions.",
                            }
                        ],
                        "is_error": True,
                    }
                project_id = cur_exp.project_id

                target_row = await db.execute(
                    select(SessionModel).where(SessionModel.id == target_session_id)
                )
                target_session = target_row.scalar_one_or_none()
                if not target_session:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Session {target_session_id} not found.",
                            }
                        ],
                        "is_error": True,
                    }

                target_exp_row = await db.execute(
                    select(Experiment).where(
                        Experiment.id == target_session.experiment_id
                    )
                )
                target_exp = target_exp_row.scalar_one_or_none()
                if not target_exp or target_exp.project_id != project_id:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Session {target_session_id} does not belong to the "
                                    f"current project. Cross-project reads are not allowed."
                                ),
                            }
                        ],
                        "is_error": True,
                    }
        except Exception as e:
            logger.exception("read_session_file db check failed")
            return {
                "content": [{"type": "text", "text": f"DB error: {e}"}],
                "is_error": True,
            }

        try:
            await reload_volume_async()
            raw = await read_volume_file_async(normalized)
        except Exception as e:
            return {
                "content": [
                    {"type": "text", "text": f"Could not read {normalized}: {e}"}
                ],
                "is_error": True,
            }

        total_bytes = len(raw)

        if _is_text_path(normalized):
            text = raw.decode("utf-8", errors="replace")
            sliced = text[offset : offset + limit]
            truncated = offset + len(sliced) < len(text)
            envelope = {
                "session_id": target_session_id,
                "path": normalized,
                "encoding": "utf-8",
                "total_bytes": total_bytes,
                "offset": offset,
                "returned_chars": len(sliced),
                "truncated": truncated,
            }
            header = json.dumps(envelope, default=str)
            return {"content": [{"type": "text", "text": f"{header}\n\n{sliced}"}]}

        # Binary — base64-encode a head slice only.
        head = raw[:_MAX_BINARY_BYTES]
        envelope = {
            "session_id": target_session_id,
            "path": normalized,
            "encoding": "base64",
            "total_bytes": total_bytes,
            "returned_bytes": len(head),
            "truncated": total_bytes > len(head),
            "note": "Binary file returned as base64; decode before use.",
        }
        payload = base64.b64encode(head).decode("ascii")
        header = json.dumps(envelope, default=str)
        return {"content": [{"type": "text", "text": f"{header}\n\n{payload}"}]}

    return handler
