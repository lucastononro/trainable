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
from services.s3_client import get_s3_client
from services.volume import (
    get_volume,
    listdir_async,
    reload_volume,
    reload_volume_async,
    remove_volume_file_async,
)

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
        sandbox_config=body.sandbox_config.model_dump() if body.sandbox_config else {},
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
    if body.sandbox_config is not None:
        project.sandbox_config = body.sandbox_config.model_dump()
    project.updated_at = _now()

    await db.commit()
    return project.to_dict()


def _purge_project_storage(project_id: str) -> dict:
    """Remove all files belonging to a project from Modal Volume and S3.

    Called from DELETE /projects/{id}. Each layer is best-effort: if S3 is
    unavailable we still try Modal, and vice versa. Returns a summary dict
    that is logged (and could be surfaced to the UI later).
    """
    summary = {
        "modal_projects_removed": False,
        "modal_sessions_removed": 0,
        "s3_objects_deleted": 0,
        "errors": [],
    }

    # --- Modal Volume -----------------------------------------------------
    # Project-scoped dataset folder: /projects/{pid}/
    # Session workspaces that belong to this project's experiments live
    # under /sessions/{session_id}/ — we collect those first because the
    # mapping is stored in Postgres, which we're about to cascade-delete.
    try:
        vol = get_volume()
        try:
            vol.remove_file(f"/projects/{project_id}", recursive=True)
            summary["modal_projects_removed"] = True
        except FileNotFoundError:
            pass
        except Exception as e:
            summary["errors"].append(f"modal projects: {e}")
    except Exception as e:
        summary["errors"].append(f"modal init: {e}")

    # --- S3 ---------------------------------------------------------------
    # datasets/projects/{pid}/... covers every file uploaded to this project.
    try:
        s3 = get_s3_client()
        prefix = f"datasets/projects/{project_id}/"
        paginator = s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket="datasets", Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        # S3 DeleteObjects max 1000 per call.
        for i in range(0, len(keys), 1000):
            chunk = keys[i : i + 1000]
            s3.delete_objects(
                Bucket="datasets",
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )
            summary["s3_objects_deleted"] += len(chunk)
    except Exception as e:
        summary["errors"].append(f"s3: {e}")

    return summary


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a project, cascade-delete its experiments (via ORM relationship),
    and purge the project's data from Modal Volume + S3.

    Storage cleanup runs BEFORE the DB delete so if the wipe raises we don't
    orphan the files. If storage cleanup succeeds but the DB commit fails,
    the caller gets the error; the files are gone but that's idempotent.
    """
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.experiments))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Also collect the session IDs before the cascade so we can wipe their
    # /sessions/{sid}/ folders in the volume.
    session_ids: list[str] = []
    exp_result = await db.execute(
        select(SessionModel.id)
        .join(Experiment, SessionModel.experiment_id == Experiment.id)
        .where(Experiment.project_id == project_id)
    )
    session_ids = [row[0] for row in exp_result.all()]

    storage = _purge_project_storage(project_id)

    # Best-effort cleanup of per-session workspaces on the volume.
    try:
        for sid in session_ids:
            try:
                await remove_volume_file_async(f"/sessions/{sid}")
                storage["modal_sessions_removed"] += 1
            except FileNotFoundError:
                pass
            except Exception as e:
                storage["errors"].append(f"modal sessions/{sid}: {e}")
    except Exception as e:
        storage["errors"].append(f"modal sessions init: {e}")

    logger.info(
        "[DELETE /projects/%s] storage cleanup: %s",
        project_id,
        storage,
    )

    await db.delete(project)
    await db.commit()
    return {"deleted": True, "storage": storage}


@router.get("/projects/{project_id}/files")
async def list_project_files(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all files uploaded to this project.

    Modal Volume is the **primary** source — that's what agent sandboxes
    actually read from, so files that are here will work. S3 is checked
    only to surface files that were uploaded to storage but didn't make it
    into the sandbox (rare; usually a Modal SDK hiccup).
    """
    # Verify the project exists.
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    datasets_root = f"/projects/{project_id}/datasets"
    prefix_in_entry = datasets_root.lstrip("/") + "/"
    s3_prefix = f"datasets/projects/{project_id}/"

    def _strip_prefix(p: str, pre: str) -> str:
        p = p.lstrip("/")
        return p[len(pre) :] if p.startswith(pre) else p

    # --- Modal Volume (primary — what agents see) -----------------------
    files_by_relpath: dict[str, dict] = {}
    sandbox_error: str | None = None
    sandbox_checked = False
    try:
        await reload_volume_async()
        for entry in await listdir_async(datasets_root, recursive=True):
            if entry.type.name != "FILE":
                continue
            rel_path = _strip_prefix(entry.path, prefix_in_entry)
            if not rel_path:
                continue
            files_by_relpath[rel_path] = {
                "path": "/" + entry.path.lstrip("/"),
                "name": rel_path.split("/")[-1],
                "relative_path": rel_path,
                "size": getattr(entry, "size", None),
                "mtime": getattr(entry, "mtime", None),
                "in_sandbox": True,
            }
        sandbox_checked = True
    except FileNotFoundError:
        # Folder genuinely hasn't been created yet — sandbox is empty.
        sandbox_checked = True
    except Exception as e:
        logger.debug("list_project_files volume listdir skipped: %s", e)
        sandbox_error = str(e)

    # --- S3 (secondary — catches files that didn't reach the sandbox) ---
    s3_error: str | None = None
    try:
        s3 = get_s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket="datasets", Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel_path = _strip_prefix(key, s3_prefix)
                if not rel_path or rel_path.endswith("/"):
                    continue
                existing = files_by_relpath.get(rel_path)
                if existing is not None:
                    # Enrich with S3 metadata; keep "in_sandbox: True".
                    if existing.get("size") is None:
                        existing["size"] = obj.get("Size")
                    if existing.get("mtime") is None and obj.get("LastModified"):
                        existing["mtime"] = obj["LastModified"].timestamp()
                    existing.setdefault("s3_key", key)
                else:
                    # File in S3 but not in Modal Volume. Only flag as
                    # "not synced" if we actually verified the sandbox.
                    files_by_relpath[rel_path] = {
                        "path": f"{datasets_root}/{rel_path}",
                        "name": rel_path.split("/")[-1],
                        "relative_path": rel_path,
                        "size": obj.get("Size"),
                        "mtime": (
                            obj["LastModified"].timestamp()
                            if obj.get("LastModified")
                            else None
                        ),
                        "s3_key": key,
                        "in_sandbox": False if sandbox_checked else None,
                    }
    except Exception as e:
        logger.warning("list_project_files S3 listing failed: %s", e)
        s3_error = str(e)

    files = sorted(files_by_relpath.values(), key=lambda f: f["relative_path"])

    sandbox_missing_count = (
        sum(1 for f in files if f["in_sandbox"] is False) if sandbox_checked else 0
    )

    return {
        "project_id": project_id,
        "project_name": project.name,
        "datasets_root": datasets_root,
        "files": files,
        "s3_error": s3_error,
        "sandbox_error": sandbox_error,
        "sandbox_checked": sandbox_checked,
        "sandbox_missing_count": sandbox_missing_count,
    }
