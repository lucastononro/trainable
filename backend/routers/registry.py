"""Model registry & deployment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from db import async_session
from models import Deployment
from services import deploy as deploy_svc
from services.registry import (
    find_session_model_artifact,
    get_model,
    list_project_models,
    promote_session_model,
)

router = APIRouter()


class PromoteRequest(BaseModel):
    name: str | None = None


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
