"""Model registry & deployment endpoints."""

from __future__ import annotations

import io
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from db import async_session
from models import Deployment, Project, RegisteredModel
from services import deploy as deploy_svc
from services.registry import (
    find_session_model_artifact,
    get_model,
    list_project_models,
    promote_session_model,
)
from services.volume import read_volume_file_async

logger = logging.getLogger(__name__)

router = APIRouter()


class PromoteRequest(BaseModel):
    name: str | None = None


@router.get("/registry/models")
async def all_models():
    """Cross-project model catalog: all registered models grouped by
    project. Powers the /models page so the user can see what's been
    trained across the whole workspace without picking a project first.
    """
    async with async_session() as db:
        models_rows = (
            (
                await db.execute(
                    select(RegisteredModel).order_by(RegisteredModel.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        project_ids = {m.project_id for m in models_rows}
        projects_rows = (
            (await db.execute(select(Project).where(Project.id.in_(project_ids))))
            .scalars()
            .all()
            if project_ids
            else []
        )
        proj_meta = {p.id: {"id": p.id, "name": p.name} for p in projects_rows}
        return {
            "projects": list(proj_meta.values()),
            "models": [
                {**m.to_dict(), "project_name": proj_meta.get(m.project_id, {}).get("name")}
                for m in models_rows
            ],
        }


@router.get("/projects/{project_id}/models")
async def project_models(project_id: str):
    return await list_project_models(project_id)


@router.get("/sessions/{session_id}/promote/check")
async def can_promote(session_id: str):
    """Return whether this session has a model artifact eligible for promotion."""
    art = await find_session_model_artifact(session_id)
    if not art:
        return {"available": False}
    return {
        "available": True,
        "path": art["path"],
        "size_bytes": art["size"],
    }


@router.post("/sessions/{session_id}/promote")
async def promote(session_id: str, body: PromoteRequest | None = None):
    try:
        return await promote_session_model(
            session_id=session_id,
            name=(body.name if body else None),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/models/{model_id}")
async def model_detail(model_id: str):
    m = await get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="Model not found")
    return m


@router.get("/models/{model_id}/download")
async def download_model(model_id: str):
    """Stream the artifact bytes back to the user. The file lives on
    the Modal volume; we read it via the volume helper and pipe it back
    with the right filename. If the volume read fails (e.g. dev-mode
    when the artifact path on the agent's machine never made it onto
    the shared volume), we 404 with the original artifact_uri so the
    user can chase it themselves.
    """
    m = await get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="Model not found")

    uri = m.get("artifact_uri") or ""
    try:
        data = await read_volume_file_async(uri)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact not found on volume at {uri}. The model row exists but the file is missing.",
        )
    except Exception as e:
        logger.warning("Failed reading model artifact %s: %s", uri, e)
        raise HTTPException(status_code=500, detail=f"Failed to read artifact: {e}")

    ext = uri.rsplit(".", 1)[-1] if "." in uri else "bin"
    filename = f"{m['name']}_v{m['version']}.{ext}"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/models/{model_id}/deploy")
async def deploy_model(model_id: str):
    try:
        return await deploy_svc.deploy_model(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/models/{model_id}/deployments")
async def model_deployments(model_id: str):
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(Deployment)
                    .where(Deployment.model_id == model_id)
                    .order_by(Deployment.id.desc())
                )
            )
            .scalars()
            .all()
        )
        return [r.to_dict() for r in rows]


@router.delete("/deployments/{deployment_id}")
async def stop_deployment(deployment_id: str):
    return await deploy_svc.stop_deployment(deployment_id)
