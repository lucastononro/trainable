"""Bulk workspace export endpoints.

Streams the contents of `/sessions/{sid}` (or every session in a project)
as a single zip the user can `cd` into and run. The zip ships a local
`trainable` shim, a filtered `requirements.txt`, and a runbook README —
so downloaded scripts that `from trainable import log, ...` don't
`NameError` on a vanilla Python install.

These endpoints inherit the auth posture of `routers/files.py`: they
read the same `/sessions/...` tree under the same caller and add no new
storage. See issue #79 for the runnability contract and rollout plan.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Project
from models import Session as SessionModel
from services.workspace_export import stream_project_zip, stream_session_zip

logger = logging.getLogger(__name__)
router = APIRouter()


def _attachment_headers(filename: str) -> dict[str, str]:
    # `Content-Disposition: attachment` is the part the browser keys on to
    # trigger a file save rather than rendering the response inline.
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@router.get("/sessions/{session_id}/download")
async def download_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    filename = f"session-{session_id[:8]}.zip"
    return StreamingResponse(
        stream_session_zip(session_id),
        media_type="application/zip",
        headers=_attachment_headers(filename),
    )


@router.get("/projects/{project_id}/download")
async def download_project(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    sessions_result = await db.execute(
        select(SessionModel.id, SessionModel.name)
        .where(SessionModel.project_id == project_id)
        .order_by(SessionModel.created_at)
    )
    rows = sessions_result.all()
    if not rows:
        raise HTTPException(
            status_code=422,
            detail="Project has no sessions to export",
        )
    sessions = [(row[0], row[1]) for row in rows]

    filename = f"project-{project_id[:8]}.zip"
    return StreamingResponse(
        stream_project_zip(project_id, sessions),
        media_type="application/zip",
        headers=_attachment_headers(filename),
    )
