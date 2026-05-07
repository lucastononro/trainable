"""Tests for services/metadata_extractor.py — automated metadata extraction."""

from contextlib import ExitStack

import pytest
from sqlalchemy import select

from tests.conftest import MockVolume, _make_parquet_bytes, mock_volume_patches


async def _seed_parents(session_id: str, experiment_id: str) -> None:
    """Insert Project + Experiment + Session rows so dependent tables can FK.

    The metadata extractor writes ProcessedDatasetMeta rows referencing both
    session_id and experiment_id; SQLite now enforces FKs, so the parents
    must exist.
    """
    from db import async_session
    from models import Experiment, Project, Session as SessionModel

    async with async_session() as db:
        project_id = f"proj-{experiment_id}"
        db.add(Project(id=project_id, name="t"))
        await db.flush()
        db.add(
            Experiment(
                id=experiment_id,
                project_id=project_id,
                name="t",
                dataset_ref="",
            )
        )
        await db.flush()
        db.add(SessionModel(id=session_id, experiment_id=experiment_id))
        await db.commit()


@pytest.mark.asyncio
async def test_extract_metadata_with_agent_metadata(mock_volume_with_prep):
    """When agent produces metadata.json, extractor should use it for target/features."""
    await _seed_parents("test-session", "test-experiment")
    with ExitStack() as stack:
        for p in mock_volume_patches(
            mock_volume_with_prep, "services.metadata_extractor"
        ):
            stack.enter_context(p)

        from services.metadata_extractor import extract_and_store_metadata

        await extract_and_store_metadata("test-session", "test-experiment")

    # Verify record was created in DB
    from db import async_session
    from models import ProcessedDatasetMeta

    async with async_session() as db:
        result = await db.execute(
            select(ProcessedDatasetMeta).where(
                ProcessedDatasetMeta.session_id == "test-session"
            )
        )
        meta = result.scalar_one()

    assert meta.session_id == "test-session"
    assert meta.experiment_id == "test-experiment"
    assert meta.target_column == "target"
    assert meta.feature_columns == ["feature_a", "feature_b"]
    assert meta.total_rows == 11  # 7 + 2 + 2
    assert meta.train_rows == 7
    assert meta.val_rows == 2
    assert meta.test_rows == 2
    assert len(meta.columns) == 3  # feature_a, feature_b, target
    assert meta.source_files  # should have found iris.csv


@pytest.mark.asyncio
async def test_extract_metadata_without_agent_metadata():
    """Without metadata.json, extractor should use heuristics for target detection."""
    await _seed_parents("s1", "exp1")
    train = _make_parquet_bytes(
        {"age": [25, 30, 35], "salary": [50, 60, 70], "target": [0, 1, 0]}
    )
    val = _make_parquet_bytes({"age": [40], "salary": [80], "target": [1]})
    test = _make_parquet_bytes({"age": [45], "salary": [90], "target": [0]})

    files = {
        "/sessions/s1/data/train.parquet": train,
        "/sessions/s1/data/val.parquet": val,
        "/sessions/s1/data/test.parquet": test,
    }
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.metadata_extractor"):
            stack.enter_context(p)

        from services.metadata_extractor import extract_and_store_metadata

        await extract_and_store_metadata("s1", "exp1")

    from db import async_session
    from models import ProcessedDatasetMeta

    async with async_session() as db:
        result = await db.execute(
            select(ProcessedDatasetMeta).where(ProcessedDatasetMeta.session_id == "s1")
        )
        meta = result.scalar_one()

    # Heuristic should detect "target" as target column
    assert meta.target_column == "target"
    assert "target" not in meta.feature_columns
    assert "age" in meta.feature_columns
    assert "salary" in meta.feature_columns
    assert meta.total_rows == 5


@pytest.mark.asyncio
async def test_extract_metadata_quality_stats(mock_volume_with_prep):
    """Quality stats should be computed from the train split."""
    await _seed_parents("test-session", "test-experiment")
    with ExitStack() as stack:
        for p in mock_volume_patches(
            mock_volume_with_prep, "services.metadata_extractor"
        ):
            stack.enter_context(p)

        from services.metadata_extractor import extract_and_store_metadata

        await extract_and_store_metadata("test-session", "test-experiment")

    from db import async_session
    from models import ProcessedDatasetMeta

    async with async_session() as db:
        result = await db.execute(
            select(ProcessedDatasetMeta).where(
                ProcessedDatasetMeta.session_id == "test-session"
            )
        )
        meta = result.scalar_one()

    stats = meta.quality_stats
    assert "missing_pct" in stats
    assert "duplicate_rows" in stats
    assert "numeric_summary" in stats
    assert "n_numeric_cols" in stats
    # feature_a and feature_b should be in numeric summary
    assert "feature_a" in stats["numeric_summary"]


@pytest.mark.asyncio
async def test_extract_metadata_no_parquet_files():
    """No parquet files should result in no DB record."""
    vol = MockVolume({})

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.metadata_extractor"):
            stack.enter_context(p)

        from services.metadata_extractor import extract_and_store_metadata

        await extract_and_store_metadata("empty-session", "exp1")

    from db import async_session
    from models import ProcessedDatasetMeta

    async with async_session() as db:
        result = await db.execute(
            select(ProcessedDatasetMeta).where(
                ProcessedDatasetMeta.session_id == "empty-session"
            )
        )
        meta = result.scalar_one_or_none()

    assert meta is None


@pytest.mark.asyncio
async def test_extract_metadata_to_dict(mock_volume_with_prep):
    """to_dict() should return a serializable dict with all fields."""
    await _seed_parents("test-session", "test-experiment")
    with ExitStack() as stack:
        for p in mock_volume_patches(
            mock_volume_with_prep, "services.metadata_extractor"
        ):
            stack.enter_context(p)

        from services.metadata_extractor import extract_and_store_metadata

        await extract_and_store_metadata("test-session", "test-experiment")

    from db import async_session
    from models import ProcessedDatasetMeta

    async with async_session() as db:
        result = await db.execute(
            select(ProcessedDatasetMeta).where(
                ProcessedDatasetMeta.session_id == "test-session"
            )
        )
        meta = result.scalar_one()

    d = meta.to_dict()
    assert isinstance(d, dict)
    assert d["session_id"] == "test-session"
    assert d["target_column"] == "target"
    assert isinstance(d["columns"], list)
    assert isinstance(d["quality_stats"], dict)
    assert d["s3_synced"] == "pending"
