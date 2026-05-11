"""Experiment CRUD routes."""

import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from db import get_db
from models import Experiment, Message, Project, RegisteredModel
from models import Session as SessionModel
from schemas import ExperimentUpdate
from services.dataset_versions import list_for_project as list_dataset_versions
from services.dataset_versions import record_upload as record_dataset_upload
from services.s3_client import get_s3_client
from services.volume import upload_many_to_volume, upload_to_volume

logger = logging.getLogger(__name__)
router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _require_project(db: AsyncSession, project_id: str) -> Project:
    """Fetch a project or raise 400 if it doesn't exist."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=400, detail=f"Project {project_id} not found")
    return project


def _safe_relative_path(raw: str) -> str:
    """Sanitize a user-supplied relative path so it can be safely used as part
    of an S3 key / volume path.

    - Strips leading / and whitespace.
    - Normalises backslashes to forward slashes.
    - Rejects any segment that equals '..' (path-traversal guard).
    - Collapses empty segments (// becomes /).
    - Falls back to "file" if the input is empty after cleanup.
    """
    if not raw:
        return "file"
    raw = raw.replace("\\", "/").strip()
    # Drop any leading slashes (we never want an absolute path on S3 side).
    while raw.startswith("/"):
        raw = raw[1:]
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        # Don't allow escaping the project root.
        raise HTTPException(status_code=400, detail=f"Invalid path segment in: {raw!r}")
    cleaned = "/".join(parts)
    return cleaned or "file"


def _dataset_s3_key(project_id: str, relative_path: str) -> str:
    """Data is owned by the project. Every chat in the project sees the same
    files at the same path, so we don't scope by experiment_id anymore."""
    return f"datasets/projects/{project_id}/{_safe_relative_path(relative_path)}"


def _dataset_volume_path(project_id: str, relative_path: str) -> str:
    return f"/projects/{project_id}/datasets/{_safe_relative_path(relative_path)}"


def _dataset_ref_for(project_id: str, uploaded: list[str]) -> str:
    """Return single-file path when there's one upload, else the project prefix."""
    if len(uploaded) == 1:
        return uploaded[0]
    return f"s3://datasets/projects/{project_id}/"


@router.get("/experiments")
async def list_experiments(
    project_id: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List experiments with optional filters.

    `q` matches experiment name or description (case-insensitive substring).
    `tag` filters to experiments whose tags array contains the value.
    `pinned` / `archived` filter on those flags.
    """
    query = select(Experiment).options(selectinload(Experiment.sessions))
    if project_id:
        query = query.where(Experiment.project_id == project_id)
    if pinned is not None:
        query = query.where(Experiment.pinned == bool(pinned))
    if archived is None:
        # By default, hide archived experiments unless explicitly requested.
        query = query.where(
            (Experiment.archived.is_(False)) | (Experiment.archived.is_(None))
        )
    elif archived is True:
        query = query.where(Experiment.archived.is_(True))
    if q:
        like = f"%{q.lower()}%"
        from sqlalchemy import func, or_

        query = query.where(
            or_(
                func.lower(Experiment.name).like(like),
                func.lower(Experiment.description).like(like),
            )
        )
    query = query.order_by(Experiment.pinned.desc(), Experiment.created_at.desc())
    result = await db.execute(query)
    experiments = result.scalars().all()
    rows = [e.to_dict(sessions=e.sessions) for e in experiments]
    if tag:
        tag_lc = tag.strip().lower()
        rows = [r for r in rows if tag_lc in (r.get("tags") or [])]
    return rows


@router.get("/projects/{project_id}/dataset-versions")
async def project_dataset_versions(project_id: str):
    return await list_dataset_versions(project_id)


@router.post("/experiments")
async def create_experiment(
    project_id: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    instructions: str = Form(""),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    await _require_project(db, project_id)
    exp_id = str(uuid.uuid4())
    s3 = get_s3_client()
    uploaded_files = []

    # See attach_data for the rationale: defer the Modal Volume push to a
    # single batch so a folder upload of 1k+ files takes one round-trip
    # rather than one per file.
    staged: list[tuple[str, str, bytes]] = []  # (tmp_path, remote_path, content)
    try:
        for f in files:
            # The browser may send a relative path for folder uploads (e.g.
            # "mydataset/train/x.csv"). Preserve it so folder structure survives
            # in S3 and the Modal Volume.
            raw_name = f.filename or "file"
            rel_path = _safe_relative_path(raw_name)
            key = _dataset_s3_key(project_id, rel_path)

            content = b""
            chunk = await f.read(1024 * 1024)
            while chunk:
                content += chunk
                if len(content) > settings.max_upload_size_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File '{rel_path}' exceeds max upload size of {settings.max_upload_size_bytes // (1024 * 1024)}MB",
                    )
                chunk = await f.read(1024 * 1024)
            logger.info("Read %s: %d bytes", rel_path, len(content))

            # Upload to S3 (for browser / S3 explorer)
            s3.put_object(
                Bucket="datasets",
                Key=key,
                Body=content,
                ContentType=f.content_type or "application/octet-stream",
            )

            # Stash for the bulk Modal Volume upload below.
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            staged.append(
                (tmp_path, _dataset_volume_path(project_id, rel_path), content)
            )

            uploaded_files.append(f"s3://datasets/{key}")
            logger.info(
                f"Uploaded {rel_path} ({len(content)} bytes) → S3 (volume pending)"
            )

            # Record content hash for dataset versioning. Failures here must not
            # block the upload — versioning is observability, not a gate.
            try:
                await record_dataset_upload(
                    project_id=project_id,
                    path=_dataset_volume_path(project_id, rel_path),
                    content=content,
                )
            except Exception as e:
                logger.warning("dataset_versions.record_upload failed: %s", e)

        if staged:
            try:
                await upload_many_to_volume([(p, r) for p, r, _ in staged])
            except Exception as e:
                logger.warning(
                    f"Modal Volume bulk upload failed for {len(staged)} files: {e}"
                )
    finally:
        for tmp_path, _, _ in staged:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

    dataset_ref = _dataset_ref_for(project_id, uploaded_files)
    now = _now()
    experiment = Experiment(
        id=exp_id,
        project_id=project_id,
        name=name,
        description=description,
        dataset_ref=dataset_ref,
        instructions=instructions,
        created_at=now,
        updated_at=now,
    )
    db.add(experiment)

    session_id = str(uuid.uuid4())
    session = SessionModel(id=session_id, experiment_id=exp_id)
    db.add(session)

    await db.commit()

    return {
        "id": exp_id,
        "project_id": project_id,
        "name": name,
        "description": description,
        "dataset_ref": dataset_ref,
        "instructions": instructions,
        "session_id": session_id,
        "uploaded_files": uploaded_files,
    }


@router.post("/experiments/from-s3")
async def create_experiment_from_s3(
    project_id: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    instructions: str = Form(""),
    s3_path: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Create experiment referencing an existing S3 dataset."""
    await _require_project(db, project_id)

    exp_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # Parse s3://bucket/key or s3://bucket/prefix/
    match = re.match(r"s3://([^/]+)/(.+)", s3_path)
    if not match:
        raise HTTPException(status_code=400, detail=f"Invalid S3 path: {s3_path}")

    bucket = match.group(1)
    key_or_prefix = match.group(2)
    s3 = get_s3_client()

    # Sync files from S3 to Modal Volume so sandboxes can access them.
    # Preserve folder structure relative to the source prefix so a nested
    # dataset like s3://.../mydata/train/x.csv lands at
    # /projects/{pid}/datasets/train/x.csv, not a flattened x.csv.
    if key_or_prefix.endswith("/"):
        prefix = key_or_prefix
        paginator = s3.get_paginator("list_objects_v2")
        # Bulk-stage every object then push in a single Modal batch — see
        # attach_data for why per-file `upload_to_volume` is a perf trap.
        staged: list[tuple[str, str]] = []
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    obj_key = obj["Key"]
                    rel_path = (
                        obj_key[len(prefix) :]
                        if obj_key.startswith(prefix)
                        else obj_key
                    )
                    if not rel_path or rel_path.endswith("/"):
                        continue
                    data = s3.get_object(Bucket=bucket, Key=obj_key)["Body"].read()
                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        tmp.write(data)
                        tmp_path = tmp.name
                    staged.append(
                        (tmp_path, _dataset_volume_path(project_id, rel_path))
                    )
            if staged:
                try:
                    await upload_many_to_volume(staged)
                except Exception as e:
                    logger.warning(
                        f"Modal Volume bulk upload failed for {len(staged)} files: {e}"
                    )
        finally:
            for tmp_path, _ in staged:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass
    else:
        filename = key_or_prefix.split("/")[-1]
        data = s3.get_object(Bucket=bucket, Key=key_or_prefix)["Body"].read()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            await upload_to_volume(tmp_path, _dataset_volume_path(project_id, filename))
        except Exception as e:
            logger.warning(f"Modal Volume upload failed for {filename}: {e}")
        finally:
            os.unlink(tmp_path)

    now = _now()
    experiment = Experiment(
        id=exp_id,
        project_id=project_id,
        name=name,
        description=description,
        dataset_ref=s3_path,
        instructions=instructions,
        created_at=now,
        updated_at=now,
    )
    db.add(experiment)

    session = SessionModel(id=session_id, experiment_id=exp_id)
    db.add(session)

    await db.commit()

    return {
        "id": exp_id,
        "project_id": project_id,
        "name": name,
        "description": description,
        "dataset_ref": s3_path,
        "instructions": instructions,
        "session_id": session_id,
    }


@router.post("/experiments/quick")
async def quick_create_experiment(
    project_id: str = Form(...),
    name: str = Form(None),
    instructions: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Create an experiment quickly — no files required. For chat-first flow."""
    await _require_project(db, project_id)

    exp_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # Auto-generate name if not provided
    if not name:
        result = await db.execute(
            select(Experiment).where(Experiment.project_id == project_id)
        )
        count = len(result.scalars().all())
        name = f"Untitled{f' {count + 1}' if count > 0 else ''}"

    now = _now()
    experiment = Experiment(
        id=exp_id,
        project_id=project_id,
        name=name,
        description="",
        dataset_ref="",
        instructions=instructions,
        created_at=now,
        updated_at=now,
    )
    db.add(experiment)

    session = SessionModel(id=session_id, experiment_id=exp_id)
    db.add(session)

    await db.commit()

    return {
        "id": exp_id,
        "project_id": project_id,
        "name": name,
        "description": "",
        "dataset_ref": "",
        "instructions": instructions,
        "session_id": session_id,
    }


@router.post("/experiments/{experiment_id}/attach")
async def attach_data(
    experiment_id: str,
    session_id: str = Form(None),
    s3_path: str = Form(None),
    files: List[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """Attach files or S3 data to an existing experiment."""

    result = await db.execute(select(Experiment).where(Experiment.id == experiment_id))
    experiment = result.scalar_one_or_none()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    project_id = experiment.project_id
    # Data is owned by the project — every chat in the project sees the same path.
    project_data_root = f"/data/projects/{project_id}/datasets/"

    if s3_path:
        # Same logic as create_experiment_from_s3 for syncing to volume
        match = re.match(r"s3://([^/]+)/(.+)", s3_path)
        if not match:
            raise HTTPException(status_code=400, detail=f"Invalid S3 path: {s3_path}")

        bucket = match.group(1)
        key_or_prefix = match.group(2)
        s3 = get_s3_client()

        if key_or_prefix.endswith("/"):
            prefix = key_or_prefix
            paginator = s3.get_paginator("list_objects_v2")
            # Stage every object as a temp file first; THEN ship them all
            # in a single Modal Volume batch_upload. The previous serial
            # `await upload_to_volume(...)` per file did one Modal
            # round-trip per file (~1s each) — for a folder of 1k+ images
            # that meant 30+ minutes and the request looked hung from the
            # frontend's perspective.
            staged: list[tuple[str, str]] = []
            try:
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        obj_key = obj["Key"]
                        rel_path = (
                            obj_key[len(prefix) :]
                            if obj_key.startswith(prefix)
                            else obj_key
                        )
                        if not rel_path or rel_path.endswith("/"):
                            continue
                        data = s3.get_object(Bucket=bucket, Key=obj_key)["Body"].read()
                        with tempfile.NamedTemporaryFile(delete=False) as tmp:
                            tmp.write(data)
                            tmp_path = tmp.name
                        staged.append(
                            (tmp_path, _dataset_volume_path(project_id, rel_path))
                        )
                if staged:
                    try:
                        await upload_many_to_volume(staged)
                    except Exception as e:
                        logger.warning(
                            f"Modal Volume bulk upload failed for {len(staged)} files: {e}"
                        )
            finally:
                for tmp_path, _ in staged:
                    try:
                        os.unlink(tmp_path)
                    except FileNotFoundError:
                        pass
        else:
            filename = key_or_prefix.split("/")[-1]
            data = s3.get_object(Bucket=bucket, Key=key_or_prefix)["Body"].read()
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                await upload_to_volume(
                    tmp_path,
                    _dataset_volume_path(project_id, filename),
                )
            except Exception as e:
                logger.warning(f"Modal Volume upload failed for {filename}: {e}")
            finally:
                os.unlink(tmp_path)

        experiment.dataset_ref = s3_path
        experiment.updated_at = _now()
        if session_id:
            db.add(
                Message(
                    session_id=session_id,
                    role="user",
                    content=f"User attached data from S3: {s3_path}. Data is now available at {project_data_root}",
                    metadata_={
                        "event_type": "file_attached",
                        "hidden": True,
                        "files": [s3_path],
                    },
                )
            )
        await db.commit()
        return {"status": "attached", "dataset_ref": s3_path}

    elif files:
        s3 = get_s3_client()
        uploaded = []
        # Same lesson as the s3_path branch: stream every multipart file
        # to a tempfile + push to S3 immediately, but DEFER the Modal
        # Volume upload to a single batch at the end so a folder upload
        # of N files takes one Modal round-trip instead of N.
        staged: list[tuple[str, str]] = []
        try:
            for f in files:
                raw_name = f.filename or "file"
                rel_path = _safe_relative_path(raw_name)
                key = _dataset_s3_key(project_id, rel_path)
                content = await f.read()
                if len(content) > settings.max_upload_size_bytes:
                    raise HTTPException(
                        status_code=413, detail=f"File '{rel_path}' too large"
                    )

                s3.put_object(
                    Bucket="datasets",
                    Key=key,
                    Body=content,
                    ContentType=f.content_type or "application/octet-stream",
                )

                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                staged.append((tmp_path, _dataset_volume_path(project_id, rel_path)))
                uploaded.append(f"s3://datasets/{key}")

            if staged:
                try:
                    await upload_many_to_volume(staged)
                except Exception as e:
                    logger.warning(
                        f"Modal Volume bulk upload failed for {len(staged)} files: {e}"
                    )
        finally:
            for tmp_path, _ in staged:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass

        dataset_ref = _dataset_ref_for(project_id, uploaded)
        experiment.dataset_ref = dataset_ref
        experiment.updated_at = _now()
        if session_id:
            filenames = [f.filename or "file" for f in files]
            db.add(
                Message(
                    session_id=session_id,
                    role="user",
                    content=f"User attached file(s): {', '.join(filenames)}. Data is now available at {project_data_root}",
                    metadata_={
                        "event_type": "file_attached",
                        "hidden": True,
                        "files": filenames,
                    },
                )
            )
        await db.commit()
        return {
            "status": "attached",
            "dataset_ref": dataset_ref,
            "uploaded_files": uploaded,
        }

    raise HTTPException(status_code=400, detail="Provide either files or s3_path")


@router.patch("/experiments/{experiment_id}")
async def update_experiment(
    experiment_id: str,
    body: ExperimentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Rename an experiment, move it to another project, or update metadata."""
    result = await db.execute(select(Experiment).where(Experiment.id == experiment_id))
    experiment = result.scalar_one_or_none()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    if body.name is not None:
        experiment.name = body.name
    if body.description is not None:
        experiment.description = body.description
    if body.instructions is not None:
        experiment.instructions = body.instructions
    if body.tags is not None:
        # Normalize: dedupe + lower-case + strip + ≤24-char tags
        seen = set()
        cleaned = []
        for t in body.tags:
            tag = (t or "").strip().lower()
            if not tag or len(tag) > 24 or tag in seen:
                continue
            seen.add(tag)
            cleaned.append(tag)
        experiment.tags = cleaned
    if body.pinned is not None:
        experiment.pinned = bool(body.pinned)
    if body.archived is not None:
        experiment.archived = bool(body.archived)
    if body.project_id is not None and body.project_id != experiment.project_id:
        await _require_project(db, body.project_id)
        experiment.project_id = body.project_id
        # Data stays with the old project; the new project has its own data folder.
        # Runner uses /projects/{pid}/datasets/, so clear the stale dataset_ref.
        experiment.dataset_ref = ""

    experiment.updated_at = _now()
    await db.commit()

    # Return with latest session info
    result = await db.execute(
        select(Experiment)
        .where(Experiment.id == experiment_id)
        .options(selectinload(Experiment.sessions))
    )
    experiment = result.scalar_one()
    return experiment.to_dict(sessions=experiment.sessions)


@router.get("/experiments/{experiment_id}")
async def get_experiment(experiment_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Experiment)
        .where(Experiment.id == experiment_id)
        .options(selectinload(Experiment.sessions))
    )
    experiment = result.scalar_one_or_none()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return {
        **experiment.to_dict(sessions=experiment.sessions),
        "sessions": [s.to_dict() for s in experiment.sessions],
    }


@router.delete("/experiments/{experiment_id}")
async def delete_experiment(experiment_id: str, db: AsyncSession = Depends(get_db)):
    # Eager-load registered_models → deployments so the ORM cascade can
    # flush deletes in dependency order (mirror the project delete fix).
    result = await db.execute(
        select(Experiment)
        .where(Experiment.id == experiment_id)
        .options(
            selectinload(Experiment.registered_models).selectinload(
                RegisteredModel.deployments
            ),
        )
    )
    experiment = result.scalar_one_or_none()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    await db.delete(experiment)
    await db.commit()
    return {"deleted": True}
