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
    name: str | None = None,
    description: str = "",
) -> dict:
    """Persist (or de-dup) a DatasetVersion row for a raw user upload.

    Always writes `kind='raw'` and leaves source_session_id/experiment_id
    NULL — those are reserved for agent-declared processed datasets.
    Re-uploads of the same bytes return the existing row.
    """
    h = hash_bytes(content)
    async with async_session() as db:
        existing = await _existing_version(db, project_id=project_id, hash_hex=h)
        if existing:
            return existing.to_dict()

        prior = await _latest_at_path(db, project_id=project_id, path=path)
        row = DatasetVersion(
            project_id=project_id,
            kind="raw",
            name=name or path.rsplit("/", 1)[-1],
            description=description,
            hash=h,
            path=path,
            size_bytes=len(content),
            parent_id=prior.id if prior else None,
            parent_hash=prior.hash if prior else None,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.to_dict()


async def register_dataset_declared(
    *,
    experiment_id: str,
    path: str,
    name: str,
    description: str,
    role: str = "input",
    parent_dataset_id: int | None = None,
    metadata: dict | None = None,
    content_hash: str | None = None,
    size_bytes: int = 0,
) -> dict:
    """Agent-driven path: register a *processed* dataset for an experiment.

    Writes a DatasetVersion(kind='processed') row, attaches it to the
    experiment via ExperimentDataset(role=...), and (if parent_dataset_id
    is supplied) records the lineage edge. The content hash is derived
    from the file at `path` if not supplied; in tests we accept it as a
    parameter so we don't need to materialize files on the volume.
    """
    from models import Experiment, ExperimentDataset

    if not description.strip():
        raise ValueError("description is required for register-dataset")

    async with async_session() as db:
        exp = (
            await db.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalar_one_or_none()
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")

        # Auto-link single-raw fallback. When the agent forgets parent_dataset_id
        # but the project has exactly one raw DatasetVersion, attach to that one
        # so the lineage graph still connects raw → processed. Multi-raw projects
        # require explicit declaration to avoid silently picking the wrong source.
        if parent_dataset_id is None and role == "input":
            raw_rows = (
                (
                    await db.execute(
                        select(DatasetVersion).where(
                            DatasetVersion.project_id == exp.project_id,
                            DatasetVersion.kind == "raw",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(raw_rows) == 1:
                parent_dataset_id = raw_rows[0].id
                logger.info(
                    "[register-dataset] auto-linked parent_dataset_id=%s "
                    "(single raw upload in project %s)",
                    parent_dataset_id,
                    exp.project_id,
                )

        # If we have a hash already (from the agent or a hashed-on-write
        # producer), dedupe across the project. Otherwise the caller has
        # to supply size_bytes + a hash; we don't read the volume here to
        # keep the service unit-testable.
        if content_hash is None:
            raise ValueError(
                "content_hash is required — hash the file before calling register-dataset"
            )

        existing = await _existing_version(
            db, project_id=exp.project_id, hash_hex=content_hash
        )
        if existing:
            row = existing
            # Update any newly-supplied metadata fields without disturbing
            # the original raw-upload row when it's a raw dataset re-used.
            if (existing.kind or "raw") == "raw" and role == "input":
                # Don't downgrade a raw row by re-tagging; just link it.
                pass
            else:
                if name and not existing.name:
                    existing.name = name
                if description and not existing.description:
                    existing.description = description
                if metadata is not None:
                    existing.dataset_metadata = metadata
        else:
            parent = None
            if parent_dataset_id is not None:
                parent = await db.get(DatasetVersion, parent_dataset_id)
            row = DatasetVersion(
                project_id=exp.project_id,
                kind="processed",
                name=name,
                description=description,
                hash=content_hash,
                path=path,
                size_bytes=size_bytes,
                parent_id=parent.id if parent else None,
                parent_hash=parent.hash if parent else None,
                source_session_id=exp.session_id,
                source_experiment_id=experiment_id,
                dataset_metadata=metadata or {},
            )
            db.add(row)

        await db.flush()
        # Attach to the experiment via the M2M join.
        link_exists = (
            await db.execute(
                select(ExperimentDataset).where(
                    ExperimentDataset.experiment_id == experiment_id,
                    ExperimentDataset.dataset_version_id == row.id,
                    ExperimentDataset.role == role,
                )
            )
        ).scalar_one_or_none()
        if not link_exists:
            db.add(
                ExperimentDataset(
                    experiment_id=experiment_id,
                    dataset_version_id=row.id,
                    role=role,
                )
            )

        await db.commit()
        await db.refresh(row)
        return row.to_dict()


async def list_for_project(project_id: str) -> list[dict]:
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(DatasetVersion)
                    .where(DatasetVersion.project_id == project_id)
                    .order_by(desc(DatasetVersion.id))
                )
            )
            .scalars()
            .all()
        )
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
            results.append(
                await record_upload(project_id=project_id, path=path, content=content)
            )
        except Exception as e:
            logger.warning("record_upload failed for %s: %s", path, e)
    return results
