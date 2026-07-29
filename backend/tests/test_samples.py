"""Sample-dataset gallery + project-from-sample tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import app


@pytest_asyncio.fixture
async def samples_client(client):
    """The shared `client` fixture patches the S3/volume seams that
    routers.experiments uses; services.samples binds its own imports, so
    patch those too and expose the mocks for assertions."""
    mock_s3 = MagicMock()
    with (
        patch("services.samples.get_s3_client", return_value=mock_s3),
        patch(
            "services.samples.upload_many_to_volume", new_callable=AsyncMock
        ) as mock_volume,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.mock_s3 = mock_s3
            ac.mock_volume = mock_volume
            yield ac


@pytest.mark.asyncio
async def test_list_samples(samples_client):
    resp = await samples_client.get("/api/samples")
    assert resp.status_code == 200
    samples = resp.json()
    assert len(samples) >= 2
    by_id = {s["id"]: s for s in samples}
    # The issue asks for a tabular demo alongside the CV one.
    assert "titanic" in by_id
    assert "license-plates" in by_id
    titanic = by_id["titanic"]
    assert titanic["name"]
    assert titanic["task"] == "classification"
    assert titanic["suggested_prompt"]
    # Repo checkout ships sample-data/, so files must resolve.
    assert titanic["available"] is True
    assert titanic["file_count"] >= 1
    assert titanic["size_bytes"] > 0


@pytest.mark.asyncio
async def test_create_project_from_sample(samples_client):
    resp = await samples_client.post(
        "/api/projects/from-sample", json={"sample_id": "titanic"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    project = body["project"]
    experiment = body["experiment"]
    assert project["name"] == "Titanic Survival"
    assert experiment["project_id"] == project["id"]
    assert body["session_id"]
    assert body["sample_id"] == "titanic"
    assert body["suggested_prompt"]

    # Dataset landed at the project-owned paths through the normal path.
    assert body["uploaded_files"]
    assert all(
        f.startswith(f"s3://datasets/datasets/projects/{project['id']}/")
        for f in body["uploaded_files"]
    )
    assert any("titanic.csv" in f for f in body["uploaded_files"])
    assert experiment["dataset_ref"]
    assert samples_client.mock_s3.put_object.call_count == len(body["uploaded_files"])
    samples_client.mock_volume.assert_awaited_once()
    volume_paths = [r for _, r in samples_client.mock_volume.await_args.args[0]]
    assert f"/projects/{project['id']}/datasets/titanic.csv" in volume_paths

    # The project + experiment are visible through the normal read paths.
    resp = await samples_client.get(f"/api/projects/{project['id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["name"] == "Titanic Survival"
    assert len(detail["experiments"]) == 1

    resp = await samples_client.get(f"/api/experiments/{experiment['id']}")
    assert resp.status_code == 200
    assert resp.json()["dataset_ref"] == experiment["dataset_ref"]


@pytest.mark.asyncio
async def test_create_project_from_sample_custom_name(samples_client):
    resp = await samples_client.post(
        "/api/projects/from-sample",
        json={"sample_id": "wine-quality", "name": "My wine project"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project"]["name"] == "My wine project"
    # Both CSVs + CONTEXT.md ship for wine-quality.
    assert len(body["uploaded_files"]) == 3
    # Multi-file uploads get the project-prefix dataset_ref.
    assert body["experiment"]["dataset_ref"].endswith("/")


@pytest.mark.asyncio
async def test_create_project_from_unknown_sample(samples_client):
    resp = await samples_client.post(
        "/api/projects/from-sample", json={"sample_id": "nope"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_project_from_sample_missing_files(samples_client):
    with patch("services.samples.sample_data_root", return_value=None):
        resp = await samples_client.post(
            "/api/projects/from-sample", json={"sample_id": "titanic"}
        )
    assert resp.status_code == 503
