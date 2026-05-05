"""Dataset versioning — record content hashes for every file uploaded.

A re-upload of the same bytes is deduped on (project_id, hash). An edited
upload (different bytes, same path) becomes a new row with `parent_hash`
linking back to the previous version at that path.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Iterable

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db import async_session
from models import DatasetVersion

logger = logging.getLogger(__name__)


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _existing_version(
    db: AsyncSession, *, project_id: str, hash_hex: str
) -> DatasetVersion | None:
    return (
        await db.execute(
            select(DatasetVersion).where(
                DatasetVersion.project_id == project_id,
                DatasetVersion.hash == hash_hex,
            )
        )
    ).scalar_one_or_none()


async def _latest_at_path(
    db: AsyncSession, *, project_id: str, path: str
) -> DatasetVersion | None:
    return (
        await db.execute(
            select(DatasetVersion)
            .where(
                DatasetVersion.project_id == project_id,
                DatasetVersion.path == path,
            )
            .order_by(desc(DatasetVersion.id))
            .limit(1)
        )
    ).scalar_one_or_none()


async def record_upload(
    *,
    project_id: str,
    path: str,
    content: bytes,
) -> dict:
    """Persist (or de-dup) a DatasetVersion row for an upload.

    Returns the row's to_dict(). Safe to call from inside an existing
    request handler — opens its own short-lived session.
    """
    h = hash_bytes(content)
    async with async_session() as db:
        existing = await _existing_version(db, project_id=project_id, hash_hex=h)
        if existing:
            return existing.to_dict()

        prior = await _latest_at_path(db, project_id=project_id, path=path)
        row = DatasetVersion(
            project_id=project_id,
            hash=h,
            path=path,
            size_bytes=len(content),
            parent_hash=prior.hash if prior else None,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.to_dict()


async def list_for_project(project_id: str) -> list[dict]:
    async with async_session() as db:
        rows = (
            await db.execute(
                select(DatasetVersion)
                .where(DatasetVersion.project_id == project_id)
                .order_by(desc(DatasetVersion.id))
            )
        ).scalars().all()
        return [r.to_dict() for r in rows]


async def record_uploads(
    *,
    project_id: str,
    items: Iterable[tuple[str, bytes]],
) -> list[dict]:
    """Bulk variant — accepts (path, content) pairs."""
    results = []
    for path, content in items:
        try:
            results.append(await record_upload(project_id=project_id, path=path, content=content))
        except Exception as e:
            logger.warning("record_upload failed for %s: %s", path, e)
    return results
