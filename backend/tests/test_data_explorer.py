"""Tests for routers/data_explorer.py — DuckDB query and preview endpoints."""

from contextlib import ExitStack

import pytest

from tests.conftest import MockVolume, mock_volume_patches


@pytest.mark.asyncio
async def test_preview_prep_data(
    client, sample_csv, mock_volume_with_prep, default_project_id
):
    # Create experiment to have a session
    exp_id, session_id = await _create_experiment(
        client, sample_csv, default_project_id
    )

    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_prep, "routers.data_explorer"):
            stack.enter_context(p)

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
    vol = MockVolume({})

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.get(
            "/api/sessions/nonexistent/prep/preview",
            params={"split": "train"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_query_prep_data(client, sample_csv, mock_volume_with_prep):
    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_prep, "routers.data_explorer"):
            stack.enter_context(p)

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
    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_prep, "routers.data_explorer"):
            stack.enter_context(p)

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
    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_prep, "routers.data_explorer"):
            stack.enter_context(p)

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
    with ExitStack() as stack:
        for p in mock_volume_patches(mock_volume_with_prep, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.post(
            "/api/sessions/test-session/prep/query",
            json={"sql": "SELECT * FROM nonexistent_table"},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_no_data(client, sample_csv):
    vol = MockVolume({})

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.post(
            "/api/sessions/empty/prep/query",
            json={"sql": "SELECT 1"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_prep_metadata_not_found(client, sample_csv, default_project_id):
    exp_id, session_id = await _create_experiment(
        client, sample_csv, default_project_id
    )

    resp = await client.get(f"/api/sessions/{session_id}/prep/metadata")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_prep_metadata_after_extraction(
    client, sample_csv, sample_parquet_splits, sample_metadata_json, default_project_id
):
    exp_id, session_id = await _create_experiment(
        client, sample_csv, default_project_id
    )

    # Build a mock volume with the actual session/experiment IDs
    vol = MockVolume(
        {
            f"/sessions/{session_id}/data/train.parquet": sample_parquet_splits[
                "train"
            ],
            f"/sessions/{session_id}/data/val.parquet": sample_parquet_splits["val"],
            f"/sessions/{session_id}/data/test.parquet": sample_parquet_splits["test"],
            f"/sessions/{session_id}/data/metadata.json": sample_metadata_json,
            f"/datasets/{exp_id}/iris.csv": b"a,b,target\n1,2,0\n",
        }
    )

    # Run metadata extraction
    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.metadata_extractor"):
            stack.enter_context(p)

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
# Raw dataset preview (pre-prep)
# ---------------------------------------------------------------------------

_RAW_CSV = b"a,b,c\n1,x,\n2,,3.5\n3,x,4.5\n4,y,\n"


def _make_raw_parquet() -> bytes:
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "num": pa.array([1.5, 2.5, None, 4.5]),
            "cat": pa.array(["a", "b", "a", "b"]),
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _raw_volume(project_id: str) -> MockVolume:
    root = f"/projects/{project_id}/datasets"
    return MockVolume(
        {
            f"{root}/data.csv": _RAW_CSV,
            f"{root}/folder/data.parquet": _make_raw_parquet(),
            f"{root}/notes.txt": b"not tabular",
        }
    )


@pytest.mark.asyncio
async def test_raw_preview_csv_profile(client, default_project_id):
    vol = _raw_volume(default_project_id)
    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.get(
            f"/api/projects/{default_project_id}/datasets/preview",
            params={"path": "data.csv", "limit": 3},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "data.csv"
    assert body["format"] == "csv"
    assert body["path"] == f"/projects/{default_project_id}/datasets/data.csv"
    assert body["row_count"] == 4
    assert body["column_count"] == 3
    assert body["head_columns"] == ["a", "b", "c"]
    assert len(body["head_rows"]) == 3  # limit respected

    cols = {c["name"]: c for c in body["columns"]}
    assert set(cols) == {"a", "b", "c"}
    # dtypes inferred by DuckDB
    assert "INT" in cols["a"]["dtype"].upper()
    assert cols["b"]["dtype"].upper() == "VARCHAR"
    # missing % per column
    assert cols["a"]["missing_pct"] == 0.0
    assert cols["b"]["missing_pct"] == pytest.approx(25.0)
    assert cols["c"]["missing_pct"] == pytest.approx(50.0)
    # cardinality (approx, exact at this scale)
    assert cols["a"]["unique_count"] == 4
    assert cols["b"]["unique_count"] == 2
    # nulls serialize as JSON null in head rows
    assert body["head_rows"][0][2] is None


@pytest.mark.asyncio
async def test_raw_preview_parquet_absolute_path(client, default_project_id):
    vol = _raw_volume(default_project_id)
    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.get(
            f"/api/projects/{default_project_id}/datasets/preview",
            params={
                "path": f"/projects/{default_project_id}/datasets/folder/data.parquet"
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format"] == "parquet"
    assert body["row_count"] == 4
    assert body["column_count"] == 2
    cols = {c["name"]: c for c in body["columns"]}
    assert cols["num"]["missing_pct"] == pytest.approx(25.0)
    assert cols["cat"]["unique_count"] == 2


@pytest.mark.asyncio
async def test_raw_preview_rejects_traversal(client, default_project_id):
    vol = _raw_volume(default_project_id)
    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.get(
            f"/api/projects/{default_project_id}/datasets/preview",
            params={"path": "../../other-project/datasets/data.csv"},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_raw_preview_unsupported_extension(client, default_project_id):
    vol = _raw_volume(default_project_id)
    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.get(
            f"/api/projects/{default_project_id}/datasets/preview",
            params={"path": "notes.txt"},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_raw_preview_file_not_found(client, default_project_id):
    vol = _raw_volume(default_project_id)
    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.get(
            f"/api/projects/{default_project_id}/datasets/preview",
            params={"path": "missing.csv"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_raw_preview_project_not_found(client):
    vol = MockVolume({})
    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "routers.data_explorer"):
            stack.enter_context(p)

        resp = await client.get(
            "/api/projects/nonexistent/datasets/preview",
            params={"path": "data.csv"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _create_experiment(client, sample_csv, project_id):
    with open(sample_csv, "rb") as f:
        resp = await client.post(
            "/api/experiments",
            data={
                "project_id": project_id,
                "name": "Test",
                "description": "",
                "instructions": "",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    body = resp.json()
    return body["id"], body["session_id"]
