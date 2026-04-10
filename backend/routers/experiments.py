"""Experiment CRUD routes."""

import logging
import os
import re
import tempfile
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from db import get_db
from models import Experiment, Message
from models import Session as SessionModel
from services.s3_client import get_s3_client
from services.volume import upload_to_volume

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/experiments")
async def list_experiments(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Experiment)
        .options(selectinload(Experiment.sessions))
        .order_by(Experiment.created_at.desc())
    )
    experiments = result.scalars().all()
    return [e.to_dict(sessions=e.sessions) for e in experiments]


@router.post("/experiments")
async def create_experiment(
    name: str = Form(...),
    description: str = Form(""),
    instructions: str = Form(""),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    exp_id = str(uuid.uuid4())
    s3 = get_s3_client()
    uploaded_files = []

    for f in files:
        filename = f.filename or "file"
        key = f"datasets/{exp_id}/{filename}"

        content = b""
        chunk = await f.read(1024 * 1024)
        while chunk:
            content += chunk
            if len(content) > settings.max_upload_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{filename}' exceeds max upload size of {settings.max_upload_size_bytes // (1024 * 1024)}MB",
                )
            chunk = await f.read(1024 * 1024)
        logger.info("Read %s: %d bytes", filename, len(content))

        # Upload to S3 (for browser / S3 explorer)
        s3.put_object(
            Bucket="datasets",
            Key=key,
            Body=content,
            ContentType=f.content_type or "application/octet-stream",
        )

        # Upload to Modal Volume (for sandbox execution)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            await upload_to_volume(tmp_path, f"/datasets/{exp_id}/{filename}")
        except Exception as e:
            logger.warning(f"Modal Volume upload failed for {filename}: {e}")
        finally:
            os.unlink(tmp_path)

        uploaded_files.append(f"s3://datasets/{key}")
        logger.info(f"Uploaded {filename} ({len(content)} bytes) → S3 + Modal Volume")

    # dataset_ref: folder for multiple files, single file path otherwise
    if len(uploaded_files) == 1:
        dataset_ref = uploaded_files[0]
    else:
        dataset_ref = f"s3://datasets/datasets/{exp_id}/"

    experiment = Experiment(
        id=exp_id,
        name=name,
        description=description,
        dataset_ref=dataset_ref,
        instructions=instructions,
    )
    db.add(experiment)

    session_id = str(uuid.uuid4())
    session = SessionModel(id=session_id, experiment_id=exp_id)
    db.add(session)

    await db.commit()

    return {
        "id": exp_id,
        "name": name,
        "description": description,
        "dataset_ref": dataset_ref,
        "instructions": instructions,
        "session_id": session_id,
        "uploaded_files": uploaded_files,
    }


@router.post("/experiments/from-s3")
async def create_experiment_from_s3(
    name: str = Form(...),
    description: str = Form(""),
    instructions: str = Form(""),
    s3_path: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Create experiment referencing an existing S3 dataset."""

    exp_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # Parse s3://bucket/key or s3://bucket/prefix/
    match = re.match(r"s3://([^/]+)/(.+)", s3_path)
    if not match:
        raise HTTPException(status_code=400, detail=f"Invalid S3 path: {s3_path}")

    bucket = match.group(1)
    key_or_prefix = match.group(2)
    s3 = get_s3_client()

    # Sync files from S3 to Modal Volume so sandboxes can access them
    if key_or_prefix.endswith("/"):
        response = s3.list_objects_v2(Bucket=bucket, Prefix=key_or_prefix)
        for obj in response.get("Contents", []):
            obj_key = obj["Key"]
            filename = obj_key.split("/")[-1]
            if not filename:
                continue
            data = s3.get_object(Bucket=bucket, Key=obj_key)["Body"].read()
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                await upload_to_volume(tmp_path, f"/datasets/{exp_id}/{filename}")
            except Exception as e:
                logger.warning(f"Modal Volume upload failed for {filename}: {e}")
            finally:
                os.unlink(tmp_path)
    else:
        filename = key_or_prefix.split("/")[-1]
        data = s3.get_object(Bucket=bucket, Key=key_or_prefix)["Body"].read()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            await upload_to_volume(tmp_path, f"/datasets/{exp_id}/{filename}")
        except Exception as e:
            logger.warning(f"Modal Volume upload failed for {filename}: {e}")
        finally:
            os.unlink(tmp_path)

    experiment = Experiment(
        id=exp_id,
        name=name,
        description=description,
        dataset_ref=s3_path,
        instructions=instructions,
    )
    db.add(experiment)

    session = SessionModel(id=session_id, experiment_id=exp_id)
    db.add(session)

    await db.commit()

    return {
        "id": exp_id,
        "name": name,
        "description": description,
        "dataset_ref": s3_path,
        "instructions": instructions,
        "session_id": session_id,
    }


@router.post("/experiments/quick")
async def quick_create_experiment(
    name: str = Form(None),
    instructions: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Create an experiment quickly — no files required. For chat-first flow."""

    exp_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # Auto-generate name if not provided
    if not name:
        result = await db.execute(select(Experiment))
        count = len(result.scalars().all())
        name = f"Untitled{f' {count + 1}' if count > 0 else ''}"

    experiment = Experiment(
        id=exp_id,
        name=name,
        description="",
        dataset_ref="",
        instructions=instructions,
    )
    db.add(experiment)

    session = SessionModel(id=session_id, experiment_id=exp_id)
    db.add(session)

    await db.commit()

    return {
        "id": exp_id,
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

    if s3_path:
        # Same logic as create_experiment_from_s3 for syncing to volume
        match = re.match(r"s3://([^/]+)/(.+)", s3_path)
        if not match:
            raise HTTPException(status_code=400, detail=f"Invalid S3 path: {s3_path}")

        bucket = match.group(1)
        key_or_prefix = match.group(2)
        s3 = get_s3_client()

        if key_or_prefix.endswith("/"):
            response = s3.list_objects_v2(Bucket=bucket, Prefix=key_or_prefix)
            for obj in response.get("Contents", []):
                obj_key = obj["Key"]
                filename = obj_key.split("/")[-1]
                if not filename:
                    continue
                data = s3.get_object(Bucket=bucket, Key=obj_key)["Body"].read()
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name
                try:
                    await upload_to_volume(tmp_path, f"/datasets/{experiment_id}/{filename}")
                except Exception as e:
                    logger.warning(f"Modal Volume upload failed for {filename}: {e}")
                finally:
                    os.unlink(tmp_path)
        else:
            filename = key_or_prefix.split("/")[-1]
            data = s3.get_object(Bucket=bucket, Key=key_or_prefix)["Body"].read()
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                await upload_to_volume(tmp_path, f"/datasets/{experiment_id}/{filename}")
            except Exception as e:
                logger.warning(f"Modal Volume upload failed for {filename}: {e}")
            finally:
                os.unlink(tmp_path)

        experiment.dataset_ref = s3_path
        if session_id:
            db.add(
                Message(
                    session_id=session_id,
                    role="user",
                    content=f"User attached data from S3: {s3_path}. Data is now available at /data/datasets/{experiment_id}/",
                    metadata_={"event_type": "file_attached", "hidden": True, "files": [s3_path]},
                )
            )
        await db.commit()
        return {"status": "attached", "dataset_ref": s3_path}

    elif files:
        s3 = get_s3_client()
        uploaded = []
        for f in files:
            filename = f.filename or "file"
            key = f"datasets/{experiment_id}/{filename}"
            content = await f.read()
            if len(content) > settings.max_upload_size_bytes:
                raise HTTPException(status_code=413, detail=f"File '{filename}' too large")

            s3.put_object(
                Bucket="datasets",
                Key=key,
                Body=content,
                ContentType=f.content_type or "application/octet-stream",
            )

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                await upload_to_volume(tmp_path, f"/datasets/{experiment_id}/{filename}")
            except Exception as e:
                logger.warning(f"Modal Volume upload failed for {filename}: {e}")
            finally:
                os.unlink(tmp_path)

            uploaded.append(f"s3://datasets/{key}")

        dataset_ref = uploaded[0] if len(uploaded) == 1 else f"s3://datasets/datasets/{experiment_id}/"
        experiment.dataset_ref = dataset_ref
        if session_id:
            filenames = [f.filename or "file" for f in files]
            db.add(
                Message(
                    session_id=session_id,
                    role="user",
                    content=f"User attached file(s): {', '.join(filenames)}. Data is now available at /data/datasets/{experiment_id}/",
                    metadata_={"event_type": "file_attached", "hidden": True, "files": filenames},
                )
            )
        await db.commit()
        return {"status": "attached", "dataset_ref": dataset_ref, "uploaded_files": uploaded}

    raise HTTPException(status_code=400, detail="Provide either files or s3_path")


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
    result = await db.execute(select(Experiment).where(Experiment.id == experiment_id))
    experiment = result.scalar_one_or_none()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    await db.delete(experiment)
    await db.commit()
    return {"deleted": True}
