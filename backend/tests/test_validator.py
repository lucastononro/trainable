"""Tests for services/validator.py — automated post-agent validation."""

import json
from contextlib import ExitStack

import pytest

from tests.conftest import MockVolume, _make_parquet_bytes, mock_volume_patches


@pytest.mark.asyncio
async def test_validate_prep_output_all_good(mock_volume_with_prep):
    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_prep, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_prep_output

        result = await validate_prep_output("test-session", "test-experiment")

    assert result["stage"] == "prep"
    assert len(result["errors"]) == 0
    assert len(result["passed"]) > 0
    # Should pass: files exist, schema match, no nulls, metadata ok, no leakage
    passed_texts = " ".join(result["passed"])
    assert "train.parquet exists" in passed_texts
    assert "val.parquet exists" in passed_texts
    assert "test.parquet exists" in passed_texts
    assert "No null values" in passed_texts
    assert "metadata.json exists" in passed_texts


@pytest.mark.asyncio
async def test_validate_prep_output_missing_file():
    """Missing val.parquet should produce an error."""
    files = {
        "/sessions/s1/data/train.parquet": _make_parquet_bytes(
            {"x": [1, 2], "y": [0, 1]}
        ),
        "/sessions/s1/data/test.parquet": _make_parquet_bytes(
            {"x": [3, 4], "y": [1, 0]}
        ),
    }
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_prep_output

        result = await validate_prep_output("s1", "exp1")

    error_texts = " ".join(result["errors"])
    assert "val.parquet missing" in error_texts


@pytest.mark.asyncio
async def test_validate_prep_output_schema_mismatch():
    """Different schemas across splits should produce an error."""
    train = _make_parquet_bytes({"x": [1, 2], "y": [0, 1]})
    val = _make_parquet_bytes({"x": [3, 4], "z": [1, 0]})  # z instead of y
    test = _make_parquet_bytes({"x": [5, 6], "y": [0, 1]})
    files = {
        "/sessions/s1/data/train.parquet": train,
        "/sessions/s1/data/val.parquet": val,
        "/sessions/s1/data/test.parquet": test,
    }
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_prep_output

        result = await validate_prep_output("s1", "exp1")

    error_texts = " ".join(result["errors"])
    assert "schema differs" in error_texts


@pytest.mark.asyncio
async def test_validate_prep_output_with_nulls():
    """Null values in train should be flagged as error."""
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    # Create parquet with null values using pyarrow directly
    table = pa.table(
        {
            "x": pa.array([1.0, None, 3.0]),
            "y": pa.array([0, 1, 0]),
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    train_bytes = buf.getvalue()

    val_bytes = _make_parquet_bytes({"x": [4.0, 5.0], "y": [1, 0]})
    test_bytes = _make_parquet_bytes({"x": [6.0, 7.0], "y": [0, 1]})

    files = {
        "/sessions/s1/data/train.parquet": train_bytes,
        "/sessions/s1/data/val.parquet": val_bytes,
        "/sessions/s1/data/test.parquet": test_bytes,
    }
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_prep_output

        result = await validate_prep_output("s1", "exp1")

    error_texts = " ".join(result["errors"])
    assert "Null values" in error_texts


@pytest.mark.asyncio
async def test_validate_prep_output_metadata_target_missing():
    """Target column in metadata.json not found in parquet should error."""
    train = _make_parquet_bytes({"x": [1, 2], "y": [3, 4]})
    meta = json.dumps(
        {
            "target_column": "nonexistent_col",
            "problem_type": "regression",
            "features": ["x"],
            "splits": {"train": {"rows": 2}},
        }
    ).encode()

    files = {
        "/sessions/s1/data/train.parquet": train,
        "/sessions/s1/data/val.parquet": train,
        "/sessions/s1/data/test.parquet": train,
        "/sessions/s1/data/metadata.json": meta,
    }
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_prep_output

        result = await validate_prep_output("s1", "exp1")

    error_texts = " ".join(result["errors"])
    assert "nonexistent_col" in error_texts
    assert "not found" in error_texts


@pytest.mark.asyncio
async def test_validate_prep_output_no_metadata_json():
    """Missing metadata.json should produce a warning, not an error."""
    train = _make_parquet_bytes({"x": [1, 2], "y": [3, 4]})
    val = _make_parquet_bytes({"x": [5, 6], "y": [7, 8]})
    test = _make_parquet_bytes({"x": [9, 10], "y": [11, 12]})
    files = {
        "/sessions/s1/data/train.parquet": train,
        "/sessions/s1/data/val.parquet": val,
        "/sessions/s1/data/test.parquet": test,
    }
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_prep_output

        result = await validate_prep_output("s1", "exp1")

    assert len(result["errors"]) == 0
    warning_texts = " ".join(result["warnings"])
    assert "metadata.json not found" in warning_texts


@pytest.mark.asyncio
async def test_validate_train_output_all_good(mock_volume_with_train):
    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_train, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_train_output

        result = await validate_train_output("test-session", "test-experiment")

    assert result["stage"] == "train"
    assert len(result["errors"]) == 0
    passed_texts = " ".join(result["passed"])
    assert "Model file found" in passed_texts
    assert "report.md exists" in passed_texts
    assert "metadata.json has model and test metrics" in passed_texts


@pytest.mark.asyncio
async def test_validate_train_output_no_model():
    """Missing model file should error."""
    files = {
        "/sessions/s1/report.md": b"# Report",
    }
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_train_output

        result = await validate_train_output("s1", "exp1")

    error_texts = " ".join(result["errors"])
    assert "No model file found" in error_texts


@pytest.mark.asyncio
async def test_validate_train_output_resolves_report_via_artifact_row():
    """Regression for B2: pre-fix, line 396 called `_read_volume_file_safe`
    without `await`. With an Artifact row pointing to the report, the bare
    coroutine was truthy → fallback scan skipped → `len(coroutine)` raised
    TypeError → validation aborted halfway through, so leakage/metadata
    checks below silently never ran. With the await in place, the function
    walks the entire checklist and produces `report.md exists` from the DB
    path."""
    from db import async_session
    from models import Artifact, Project, Session

    train_meta = json.dumps(
        {
            "best_model": "XGBClassifier",
            "test_metrics": {"accuracy": 0.85, "f1_weighted": 0.84},
        }
    ).encode("utf-8")
    files = {
        "/sessions/sid-art/models/model.pkl": b"fake-model",
        # Report at a non-default path — only reachable via the Artifact row.
        "/sessions/sid-art/reports/train_report.md": b"# Train Report\nXGB.",
        "/sessions/sid-art/data/metadata.json": train_meta,
    }
    vol = MockVolume(files)

    # Seed Project + Session so the Artifact FK doesn't trip.
    async with async_session() as db:
        db.add(Project(id="proj-art", name="P"))
        db.add(Session(id="sid-art", project_id="proj-art"))
        await db.commit()
        db.add(
            Artifact(
                session_id="sid-art",
                stage="train",
                artifact_type="report",
                name="train_report.md",
                path="/sessions/sid-art/reports/train_report.md",
                created_at="2026-05-12T00:00:00",
            )
        )
        await db.commit()

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_train_output

        # Pre-fix this raised TypeError("object of type 'coroutine' has no len()").
        result = await validate_train_output("sid-art", "exp-art")

    assert result["stage"] == "train"
    passed = " ".join(result["passed"])
    assert "report.md exists" in passed
    # Confirm the rest of the checklist (metadata) was reachable too —
    # the pre-fix TypeError aborted before this line.
    assert "metadata.json has model and test metrics" in passed


@pytest.mark.asyncio
async def test_validate_train_output_perfect_metrics_warning():
    """Perfect accuracy=1.0 on test should warn about overfitting."""
    meta = json.dumps(
        {
            "best_model": "SomeModel",
            "test_metrics": {"accuracy": 1.0, "f1": 0.99},
        }
    ).encode()
    files = {
        "/sessions/s1/models/model.pkl": b"model",
        "/sessions/s1/report.md": b"# Report",
        "/sessions/s1/data/metadata.json": meta,
    }
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.validator"):
            stack.enter_context(p)

        from services.validator import validate_train_output

        result = await validate_train_output("s1", "exp1")

    warning_texts = " ".join(result["warnings"])
    assert "overfitting" in warning_texts.lower()
