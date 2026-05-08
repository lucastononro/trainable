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


class DeployRequest(BaseModel):
    """Optional body for POST /api/models/{id}/deploy.

    `compute` selects the Modal target — defaults to CPU. Anything we
    don't recognize falls back to CPU at the service layer so the user
    never silently lights up an A100.
    """

    compute: str | None = None


@router.post("/models/{model_id}/deploy")
async def deploy_model(model_id: str, body: DeployRequest | None = None):
    try:
        return await deploy_svc.deploy_model(
            model_id,
            compute=(body.compute if body else None),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/deploy/compute-options")
async def deploy_compute_options():
    """List the compute targets the dropdown on /models offers. Source
    of truth for the labels + the per-option blurb lives here so the
    frontend doesn't drift from the backend."""
    return [
        {"value": "cpu", "label": "CPU", "blurb": "Default. Cheap pool, no GPU."},
        {"value": "T4", "label": "T4 (16 GB)", "blurb": "Cheapest GPU. Good for small inference."},
        {"value": "L4", "label": "L4 (24 GB)", "blurb": "Best price/perf for inference."},
        {"value": "A10G", "label": "A10G (24 GB)", "blurb": "AWS-style mid-tier."},
        {"value": "A100-40GB", "label": "A100 (40 GB)", "blurb": "Large-model inference."},
        {"value": "A100-80GB", "label": "A100 (80 GB)", "blurb": "Extra headroom for long contexts."},
        {"value": "H100", "label": "H100 (80 GB)", "blurb": "Top-tier. Premium $/hr."},
    ]


@router.get("/models/{model_id}/serving-app")
async def get_serving_app(model_id: str):
    """Return the current source of the model's Modal serving app
    (`app.py`). Used by the /models inspect/edit panel so the user can
    audit and customize what the Deploy button will ship.
    """
    from services.volume import read_volume_file_async

    m = await deploy_svc.get_model(model_id) if hasattr(deploy_svc, "get_model") else None
    # Fall back to direct DB lookup if the deploy module doesn't
    # re-export get_model — keeps the route loosely coupled.
    if m is None:
        from services.registry import get_model

        m = await get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="Model not found")
    if not m.get("serving_app_path"):
        raise HTTPException(
            status_code=404,
            detail="No serving app yet. Ask an agent to run create-serving-app first.",
        )
    try:
        data = await read_volume_file_async(m["serving_app_path"])
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"serving_app_path is set ({m['serving_app_path']}) but the file is "
                "missing on the volume — re-run create-serving-app."
            ),
        )
    return {"path": m["serving_app_path"], "code": data.decode("utf-8")}


class ServingAppUpdate(BaseModel):
    code: str


@router.put("/models/{model_id}/serving-app")
async def put_serving_app(model_id: str, body: ServingAppUpdate):
    """Save user edits to the model's serving app.py. The file is the
    source of truth for `modal deploy`, so this is what the Deploy
    button will ship next.
    """
    import ast

    from services.registry import get_model
    from services.volume import write_to_volume

    m = await get_model(model_id)
    if not m:
        raise HTTPException(status_code=404, detail="Model not found")
    if not m.get("serving_app_path"):
        raise HTTPException(
            status_code=400,
            detail="Model has no serving_app_path; create one with create-serving-app first.",
        )
    # Catch obvious syntax errors before we let the user click Deploy
    # and watch Modal reject the file.
    try:
        ast.parse(body.code)
    except SyntaxError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Refusing to save: SyntaxError on line {e.lineno}: {e.msg}",
        )
    await write_to_volume(body.code, m["serving_app_path"])
    return {"ok": True, "path": m["serving_app_path"], "size": len(body.code)}


@router.post("/models/{model_id}/rotate-key")
async def rotate_model_key(model_id: str):
    """Regenerate the X-API-Key for a model + replace the Modal secret.
    Existing live containers keep serving the old key until cold-start;
    user can click Redeploy to force the cutover."""
    try:
        return await deploy_svc.rotate_api_key(model_id)
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
