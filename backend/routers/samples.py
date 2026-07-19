"""Bundled sample-dataset gallery + one-click "project from sample" creation.

The catalog lives in `samples.yml` (one source of truth — the UI fetches the
same data). Files are copied out of the repo's `sample-data/` directory into
a fresh project through the same S3 + Modal Volume path a browser upload
takes, so the resulting project is indistinguishable from a hand-uploaded one.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import get_db
from models import Experiment, Project
from models import Session as SessionModel
from routers.experiments import (
    _dataset_ref_for,
    _dataset_s3_key,
    _dataset_volume_path,
)
from schemas import ProjectFromSample
from services.dataset_versions import record_uploads
from services.s3_client import get_s3_client
from services.volume import upload_many_to_volume

logger = logging.getLogger(__name__)
router = APIRouter()

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REGISTRY_PATH = _BACKEND_DIR / "samples.yml"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sample_data_root() -> Path | None:
    """Locate the sample-data directory, or None if it isn't shipped."""
    candidates = []
    if settings.sample_data_dir:
        candidates.append(Path(settings.sample_data_dir))
    candidates.append(_BACKEND_DIR.parent / "sample-data")  # repo checkout
    candidates.append(_BACKEND_DIR / "sample-data")  # container mount/copy
    for c in candidates:
        if c.is_dir():
            return c
    return None


@lru_cache(maxsize=1)
def _load_registry() -> list[dict]:
    """Parse samples.yml once per process (it's committed, not user data)."""
    try:
        with open(_REGISTRY_PATH) as f:
            data = yaml.safe_load(f) or {}
        return list(data.get("samples", []))
    except FileNotFoundError:
        logger.warning("samples.yml not found at %s", _REGISTRY_PATH)
        return []


def _sample_files(root: Path | None, sample: dict) -> list[tuple[str, Path]]:
    """Resolve a sample's declared files to (relative_path, absolute_path)
    pairs, keeping only the ones that actually exist on disk."""
    if root is None:
        return []
    base = root / sample["dir"]
    out: list[tuple[str, Path]] = []
    for rel in sample.get("files", []):
        p = base / rel
        if p.is_file():
            out.append((rel, p))
    return out


@router.get("/samples")
async def list_samples():
    """Sample-dataset catalog for the first-run gallery tiles."""
    root = _sample_data_root()
    out = []
    for sample in _load_registry():
        files = _sample_files(root, sample)
        out.append(
            {
                "id": sample["id"],
                "name": sample["name"],
                "task": sample.get("task", ""),
                "description": sample.get("description", ""),
                "suggested_prompt": sample.get("suggested_prompt", ""),
                "file_count": len(files),
                "size_bytes": sum(p.stat().st_size for _, p in files),
                # A sample is offerable only when every declared file exists.
                "available": bool(files)
                and len(files) == len(sample.get("files", [])),
            }
        )
    return out


@router.post("/projects/from-sample")
async def create_project_from_sample(
    body: ProjectFromSample,
    db: AsyncSession = Depends(get_db),
):
    """Create a project pre-loaded with a bundled sample dataset.

    Mirrors POST /projects (project + initial experiment + session) and the
    dataset half of POST /experiments: every sample file goes to S3 and the
    Modal Volume at the project-owned dataset paths.
    """
    sample = next(
        (s for s in _load_registry() if s["id"] == body.sample_id), None
    )
    if not sample:
        raise HTTPException(
            status_code=404, detail=f"Sample '{body.sample_id}' not found"
        )

    files = _sample_files(_sample_data_root(), sample)
    if len(files) != len(sample.get("files", [])):
        # Sample data isn't shipped in this deployment — operational, not a bug.
        raise HTTPException(
            status_code=503,
            detail=(
                f"Sample '{body.sample_id}' data files are not available on "
                "this server (sample-data/ is missing)"
            ),
        )

    project_id = str(uuid.uuid4())
    uploaded_files: list[str] = []
    s3 = get_s3_client()

    # Copy files through the normal upload path: S3 immediately, Modal Volume
    # in one deferred batch (see routers/experiments.attach_data for why).
    staged: list[tuple[str, str]] = []  # (tmp_path, remote_path)
    version_items: list[tuple[str, bytes]] = []
    try:
        for rel_path, abs_path in files:
            content = abs_path.read_bytes()
            content_type = (
                mimetypes.guess_type(rel_path)[0] or "application/octet-stream"
            )
            s3.put_object(
                Bucket="datasets",
                Key=_dataset_s3_key(project_id, rel_path),
                Body=content,
                ContentType=content_type,
            )
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(content)
                staged.append((tmp.name, _dataset_volume_path(project_id, rel_path)))
            version_items.append((_dataset_volume_path(project_id, rel_path), content))
            uploaded_files.append(
                f"s3://datasets/{_dataset_s3_key(project_id, rel_path)}"
            )

        if staged:
            try:
                await upload_many_to_volume(staged)
            except Exception as e:
                logger.warning(
                    "Modal Volume bulk upload failed for %d sample files: %s",
                    len(staged),
                    e,
                )
    finally:
        for tmp_path, _ in staged:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

    # Versioning is observability, not a gate — record_uploads is best-effort.
    await record_uploads(project_id=project_id, items=version_items)

    now = _now()
    project = Project(
        id=project_id,
        name=body.name or sample["name"],
        description=sample.get("description", ""),
        sandbox_config={},
        created_at=now,
        updated_at=now,
    )
    db.add(project)

    exp_id = str(uuid.uuid4())
    dataset_ref = _dataset_ref_for(project_id, uploaded_files)
    experiment = Experiment(
        id=exp_id,
        project_id=project_id,
        name=sample["name"],
        description=sample.get("description", ""),
        dataset_ref=dataset_ref,
        instructions="",
        created_at=now,
        updated_at=now,
    )
    db.add(experiment)

    session_id = str(uuid.uuid4())
    db.add(SessionModel(id=session_id, experiment_id=exp_id))

    await db.commit()

    return {
        "project": project.to_dict(experiment_count=1),
        "experiment": {
            "id": exp_id,
            "project_id": project_id,
            "name": sample["name"],
            "description": sample.get("description", ""),
            "dataset_ref": dataset_ref,
            "instructions": "",
            "created_at": now,
            "updated_at": now,
            "latest_session_id": session_id,
            "latest_state": "created",
        },
        "session_id": session_id,
        "sample_id": sample["id"],
        "suggested_prompt": sample.get("suggested_prompt", ""),
        "uploaded_files": uploaded_files,
    }
