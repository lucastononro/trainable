"""Tests for routers/data_explorer.py — DuckDB query and preview endpoints."""

from unittest.mock import patch

import pytest

from tests.conftest import MockVolume


@pytest.mark.asyncio
async def test_preview_prep_data(client, sample_csv, mock_volume_with_prep):
    # Create experiment to have a session
    exp_id, session_id = await _create_experiment(client, sample_csv)

    with (
        patch("routers.data_explorer.reload_volume"),
        patch("routers.data_explorer.get_volume", return_value=mock_volume_with_prep),
        patch(
            "routers.data_explorer.read_volume_file",
            side_effect=lambda p: b"".join(mock_volume_with_prep.read_file(p)),
        ),
    ):
        resp = await client.get(
            "/api/sessions/test-session/prep/preview",
            params={"split": "train", "limit": 5},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["split"] == "train"
    assert "columns" in body
    assert "feature_a" in body["columns"]
    assert "feature_b" in body["columns"]
    assert "target" in body["columns"]
    assert len(body["rows"]) <= 5
    assert body["row_count"] <= 5


@pytest.mark.asyncio
async def test_preview_not_found(client, sample_csv):
    with (
        patch("routers.data_explorer.reload_volume"),
        patch("routers.data_explorer.read_volume_file", side_effect=FileNotFoundError),
    ):
        resp = await client.get(
            "/api/sessions/nonexistent/prep/preview",
            params={"split": "train"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_query_prep_data(client, sample_csv, mock_volume_with_prep):
    with (
        patch("routers.data_explorer.reload_volume"),
        patch("routers.data_explorer.get_volume", return_value=mock_volume_with_prep),
        patch(
            "routers.data_explorer.read_volume_file",
            side_effect=lambda p: b"".join(mock_volume_with_prep.read_file(p)),
        ),
    ):
        resp = await client.post(
            "/api/sessions/test-session/prep/query",
            json={"sql": "SELECT * FROM train", "limit": 10},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "columns" in body
    assert "rows" in body
    assert body["row_count"] == 7  # 7 rows in train
    assert "train" in body["tables_available"]


@pytest.mark.asyncio
async def test_query_prep_data_with_filter(client, sample_csv, mock_volume_with_prep):
    with (
        patch("routers.data_explorer.reload_volume"),
        patch("routers.data_explorer.get_volume", return_value=mock_volume_with_prep),
        patch(
            "routers.data_explorer.read_volume_file",
            side_effect=lambda p: b"".join(mock_volume_with_prep.read_file(p)),
        ),
    ):
        resp = await client.post(
            "/api/sessions/test-session/prep/query",
            json={
                "sql": "SELECT feature_a, target FROM train WHERE target = 1",
                "limit": 100,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["columns"] == ["feature_a", "target"]
    # All returned rows should have target=1
    for row in body["rows"]:
        assert row[1] == 1


@pytest.mark.asyncio
async def test_query_prep_data_all_data_view(client, sample_csv, mock_volume_with_prep):
    with (
        patch("routers.data_explorer.reload_volume"),
        patch("routers.data_explorer.get_volume", return_value=mock_volume_with_prep),
        patch(
            "routers.data_explorer.read_volume_file",
            side_effect=lambda p: b"".join(mock_volume_with_prep.read_file(p)),
        ),
    ):
        resp = await client.post(
            "/api/sessions/test-session/prep/query",
            json={
                "sql": "SELECT COUNT(*) as cnt, split FROM all_data GROUP BY split",
                "limit": 100,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    # Should have 3 rows (train, val, test)
    assert body["row_count"] == 3


@pytest.mark.asyncio
async def test_query_prep_data_invalid_sql(client, sample_csv, mock_volume_with_prep):
    with (
        patch("routers.data_explorer.reload_volume"),
        patch("routers.data_explorer.get_volume", return_value=mock_volume_with_prep),
        patch(
            "routers.data_explorer.read_volume_file",
            side_effect=lambda p: b"".join(mock_volume_with_prep.read_file(p)),
        ),
    ):
        resp = await client.post(
            "/api/sessions/test-session/prep/query",
            json={"sql": "SELECT * FROM nonexistent_table"},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_no_data(client, sample_csv):
    with (
        patch("routers.data_explorer.reload_volume"),
        patch("routers.data_explorer.read_volume_file", side_effect=FileNotFoundError),
    ):
        resp = await client.post(
            "/api/sessions/empty/prep/query",
            json={"sql": "SELECT 1"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_prep_metadata_not_found(client, sample_csv):
    exp_id, session_id = await _create_experiment(client, sample_csv)

    resp = await client.get(f"/api/sessions/{session_id}/prep/metadata")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_prep_metadata_after_extraction(
    client, sample_csv, sample_parquet_splits, sample_metadata_json
):
    exp_id, session_id = await _create_experiment(client, sample_csv)

    # Build a mock volume with the actual session/experiment IDs
    vol = MockVolume(
        {
            f"/sessions/{session_id}/data/train.parquet": sample_parquet_splits[
                "train"
            ],
            f"/sessions/{session_id}/data/val.parquet": sample_parquet_splits[
                "val"
            ],
            f"/sessions/{session_id}/data/test.parquet": sample_parquet_splits[
                "test"
            ],
            f"/sessions/{session_id}/data/metadata.json": sample_metadata_json,
            f"/datasets/{exp_id}/iris.csv": b"a,b,target\n1,2,0\n",
        }
    )

    # Run metadata extraction (patch at metadata_extractor's import site)
    with (
        patch("services.metadata_extractor.reload_volume"),
        patch("services.metadata_extractor.get_volume", return_value=vol),
    ):
        from services.metadata_extractor import extract_and_store_metadata

        await extract_and_store_metadata(session_id, exp_id)

    # Now query the endpoint
    resp = await client.get(f"/api/sessions/{session_id}/prep/metadata")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["experiment_id"] == exp_id
    assert body["total_rows"] == 11
    assert body["target_column"] == "target"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _create_experiment(client, sample_csv):
    with open(sample_csv, "rb") as f:
        resp = await client.post(
            "/api/experiments",
            data={"name": "Test", "description": "", "instructions": ""},
            files={"files": ("data.csv", f, "text/csv")},
        )
    body = resp.json()
    return body["id"], body["session_id"]
