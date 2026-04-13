"""Project CRUD routes.

Projects are the top-level container for experiments (chats), datasets, and
models. Every experiment must belong to exactly one project.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_db
from models import Experiment, Project
from models import Session as SessionModel
from schemas import ProjectCreate, ProjectUpdate
from services.volume import get_volume

logger = logging.getLogger(__name__)
router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/projects")
async def list_projects(db: AsyncSession = Depends(get_db)):
    """List all projects with their experiment counts."""
    result = await db.execute(
        select(Project, func.count(Experiment.id))
        .outerjoin(Experiment, Experiment.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.updated_at.desc())
    )
    rows = result.all()
    return [p.to_dict(experiment_count=count) for (p, count) in rows]


@router.post("/projects")
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    """Create a new project and an initial empty session so the user can
    immediately start chatting."""
    project_id = str(uuid.uuid4())
    now = _now()
    project = Project(
        id=project_id,
        name=body.name or "New project",
        description=body.description or "",
        created_at=now,
        updated_at=now,
    )
    db.add(project)

    # Auto-create an initial experiment + session so the user has somewhere
    # to chat right away.
    exp_id = str(uuid.uuid4())
    experiment = Experiment(
        id=exp_id,
        project_id=project_id,
        name="Untitled",
        description="",
        dataset_ref="",
        instructions="",
        created_at=now,
        updated_at=now,
    )
    db.add(experiment)

    session_id = str(uuid.uuid4())
    session = SessionModel(
        id=session_id,
        experiment_id=exp_id,
    )
    db.add(session)

    await db.commit()

    return {
        "project": project.to_dict(experiment_count=1),
        "experiment": {
            "id": exp_id,
            "project_id": project_id,
            "name": "Untitled",
            "description": "",
            "dataset_ref": "",
            "instructions": "",
            "created_at": now,
            "updated_at": now,
            "latest_session_id": session_id,
            "latest_state": "created",
        },
        "session_id": session_id,
    }


@router.get("/projects/{project_id}")
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """Get one project with nested experiments."""
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.experiments).selectinload(Experiment.sessions))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    experiments = [
        e.to_dict(sessions=e.sessions)
        for e in sorted(project.experiments, key=lambda x: x.created_at or "")
    ]
    return {
        **project.to_dict(experiment_count=len(experiments)),
        "experiments": experiments,
    }


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update project name or description."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    project.updated_at = _now()

    await db.commit()
    return project.to_dict()


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a project and cascade-delete its experiments."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await db.delete(project)
    await db.commit()
    return {"deleted": True}


@router.get("/projects/{project_id}/files")
async def list_project_files(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all files under the project's datasets folder in the Modal Volume.

    Returns a flat list of `{path, name, size, experiment_id, experiment_name, updated_at}`.
    """
    # Verify the project exists.
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Build a lookup of experiment_id -> name so we can label files.
    exps_result = await db.execute(
        select(Experiment).where(Experiment.project_id == project_id)
    )
    experiments = list(exps_result.scalars().all())
    exp_names = {e.id: e.name for e in experiments}

    datasets_root = f"/projects/{project_id}/datasets"
    files: list[dict] = []
    try:
        vol = get_volume()
        vol.reload()
        for entry in vol.listdir(datasets_root, recursive=True):
            if entry.type.name != "FILE":
                continue
            # entry.path looks like "projects/{pid}/datasets/{exp_id}/{filename}"
            rel = entry.path
            parts = rel.strip("/").split("/")
            # Expect: ["projects", pid, "datasets", exp_id, ...filename]
            experiment_id = parts[3] if len(parts) >= 5 else None
            filename = parts[-1]
            files.append(
                {
                    "path": "/" + rel.lstrip("/"),
                    "name": filename,
                    "size": getattr(entry, "size", None),
                    "mtime": getattr(entry, "mtime", None),
                    "experiment_id": experiment_id,
                    "experiment_name": exp_names.get(experiment_id or ""),
                }
            )
    except Exception as e:
        # Folder likely doesn't exist yet (new project with no uploads).
        logger.debug("list_project_files volume listdir failed: %s", e)

    return {
        "project_id": project_id,
        "project_name": project.name,
        "datasets_root": datasets_root,
        "files": files,
    }
