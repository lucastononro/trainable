"""Tests for services/s3_sync.py — Modal Volume → S3 sync."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import MockVolume, mock_volume_patches


@pytest.mark.asyncio
async def test_sync_stage_to_s3(mock_volume_with_prep):
    mock_s3 = MagicMock()

    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_prep, "services.s3_sync"):
            stack.enter_context(p)
        stack.enter_context(
            patch("services.s3_sync.get_s3_client", return_value=mock_s3)
        )

        from services.s3_sync import sync_stage_to_s3

        result = await sync_stage_to_s3("test-session", "test-experiment", "prep")

    assert result["session_id"] == "test-session"
    assert result["experiment_id"] == "test-experiment"
    assert result["stage"] == "prep"
    assert result["files_synced"] > 0
    assert "s3_prefix" in result

    # Verify S3 put_object was called for each file
    assert mock_s3.put_object.call_count == result["files_synced"]

    # Check S3 keys contain the right prefix
    for call in mock_s3.put_object.call_args_list:
        key = call[1]["Key"] if "Key" in call[1] else call[0][0]
        assert "test-experiment" in key
        assert "test-session" in key


@pytest.mark.asyncio
async def test_sync_stage_to_s3_file_details(mock_volume_with_prep):
    mock_s3 = MagicMock()

    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_prep, "services.s3_sync"):
            stack.enter_context(p)
        stack.enter_context(
            patch("services.s3_sync.get_s3_client", return_value=mock_s3)
        )

        from services.s3_sync import sync_stage_to_s3

        result = await sync_stage_to_s3("test-session", "test-experiment", "prep")

    # Verify returned file details
    for f in result["files"]:
        assert "volume_path" in f
        assert "s3_key" in f
        assert "s3_uri" in f
        assert f["s3_uri"].startswith("s3://datasets/")
        assert f["size"] > 0


@pytest.mark.asyncio
async def test_sync_stage_to_s3_empty_workspace():
    """Syncing a workspace with no files should return 0 files."""
    vol = MockVolume({})
    mock_s3 = MagicMock()

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.s3_sync"):
            stack.enter_context(p)
        stack.enter_context(
            patch("services.s3_sync.get_s3_client", return_value=mock_s3)
        )

        from services.s3_sync import sync_stage_to_s3

        result = await sync_stage_to_s3("s1", "exp1", "prep")

    assert result["files_synced"] == 0
    assert mock_s3.put_object.call_count == 0


@pytest.mark.asyncio
async def test_sync_stage_to_s3_train(mock_volume_with_train):
    mock_s3 = MagicMock()

    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_train, "services.s3_sync"):
            stack.enter_context(p)
        stack.enter_context(
            patch("services.s3_sync.get_s3_client", return_value=mock_s3)
        )

        from services.s3_sync import sync_stage_to_s3

        result = await sync_stage_to_s3("test-session", "test-experiment", "train")

    assert result["stage"] == "train"
    assert result["files_synced"] > 0
    # Should include model.pkl, report.md, metadata.json, confusion_matrix.png
    synced_names = [f["volume_path"].split("/")[-1] for f in result["files"]]
    assert "model.pkl" in synced_names
    assert "report.md" in synced_names
