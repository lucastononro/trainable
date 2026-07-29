"""Reproducibility manifests — capture a session's state for replay later.

We hash the prep splits + scripts, write a JSON manifest under the session
workspace, and persist a `RunSnapshot` row. The volume itself remains the
source of truth for the artifacts; the snapshot is just a frozen pointer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from db import async_session
from models import Artifact, ProcessedDatasetMeta, RunSnapshot
from services.volume import (
    listdir_async,
    read_volume_file_async,
    reload_volume_async,
    write_to_volume,
)

logger = logging.getLogger(__name__)

_HASH_CHUNK = 1 << 20  # 1 MB


def _hash_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    for i in range(0, len(data), _HASH_CHUNK):
        h.update(data[i : i + _HASH_CHUNK])
    return h.hexdigest()


async def _collect_files(workspace: str, suffixes: tuple[str, ...]) -> list[dict]:
    """Return [{path, size, sha256}] for files matching suffixes, sorted by path."""
    out: list[dict] = []
    try:
        entries = await listdir_async(workspace, recursive=True)
    except FileNotFoundError:
        return out
    for entry in entries:
        if entry.type.name != "FILE":
            continue
        path = entry.path
        if not path.lower().endswith(suffixes):
            continue
        try:
            data = await read_volume_file_async(path)
            out.append(
                {
                    "path": path,
                    "size": len(data),
                    "sha256": _hash_bytes(data),
                }
            )
        except Exception as e:
            logger.debug("Could not hash %s: %s", path, e)
    out.sort(key=lambda f: f["path"])
    return out


def _aggregate_hash(files: list[dict]) -> str:
    """Combine per-file hashes into a stable directory hash."""
    h = hashlib.sha256()
    for f in files:
        h.update(f["path"].encode())
        h.update(b"\x00")
        h.update(f["sha256"].encode())
        h.update(b"\x00")
    return h.hexdigest()


async def _read_prep_metadata(session_id: str) -> dict:
    async with async_session() as db:
        meta = (
            await db.execute(
                select(ProcessedDatasetMeta).where(
                    ProcessedDatasetMeta.session_id == session_id
                )
            )
        ).scalar_one_or_none()
        return meta.to_dict() if meta else {}


async def _read_artifact_index(session_id: str) -> list[dict]:
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(Artifact).where(Artifact.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
        return [r.to_dict() for r in rows]


async def take_snapshot(session_id: str) -> dict:
    """Capture a reproducibility manifest for `session_id`.

    Idempotent — re-snapshots refresh the manifest in place.
    """
    await reload_volume_async()
    workspace = f"/sessions/{session_id}"

    data_files = await _collect_files(workspace, (".parquet", ".csv", ".feather"))
    code_files = await _collect_files(workspace, (".py", ".ipynb"))

    dataset_hash = _aggregate_hash(data_files) if data_files else None
    code_hash = _aggregate_hash(code_files) if code_files else None

    prep = await _read_prep_metadata(session_id)
    artifacts = await _read_artifact_index(session_id)

    manifest = {
        "session_id": session_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "hash": dataset_hash,
            "files": data_files,
            "prep_metadata": prep,
        },
        "code": {
            "hash": code_hash,
            "files": code_files,
        },
        "artifacts": artifacts,
        "schema_version": 1,
    }

    manifest_path = f"{workspace}/snapshot.json"
    try:
        await write_to_volume(
            json.dumps(manifest, indent=2, default=str).encode("utf-8"),
            manifest_path,
        )
    except Exception as e:
        logger.warning("Could not write manifest to volume: %s", e)
        manifest_path = None

    async with async_session() as db:
        existing = (
            await db.execute(
                select(RunSnapshot).where(RunSnapshot.session_id == session_id)
            )
        ).scalar_one_or_none()
        if existing:
            existing.dataset_hash = dataset_hash
            existing.code_hash = code_hash
            existing.hyperparams = (
                prep.get("hyperparams", {}) if isinstance(prep, dict) else {}
            )
            existing.manifest_uri = manifest_path
            row = existing
        else:
            row = RunSnapshot(
                session_id=session_id,
                dataset_hash=dataset_hash,
                code_hash=code_hash,
                hyperparams={},
                manifest_uri=manifest_path,
            )
            db.add(row)
        await db.commit()
        await db.refresh(row)
        return {**row.to_dict(), "manifest": manifest}


async def get_snapshot(session_id: str) -> dict | None:
    async with async_session() as db:
        row = (
            await db.execute(
                select(RunSnapshot).where(RunSnapshot.session_id == session_id)
            )
        ).scalar_one_or_none()
        return row.to_dict() if row else None
