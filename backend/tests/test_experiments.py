"""Experiment CRUD and upload tests."""

import pytest


@pytest.mark.asyncio
async def test_list_experiments_empty(client):
    resp = await client.get("/api/experiments")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_experiment_single_file(client, sample_csv, default_project_id):
    with open(sample_csv, "rb") as f:
        resp = await client.post(
            "/api/experiments",
            data={
                "project_id": default_project_id,
                "name": "Iris Test",
                "description": "Test experiment",
                "instructions": "Classify species",
            },
            files={"files": ("iris.csv", f, "text/csv")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Iris Test"
    assert body["description"] == "Test experiment"
    assert "session_id" in body
    assert body["id"]
    assert "iris.csv" in body["dataset_ref"]
    assert len(body["uploaded_files"]) == 1


@pytest.mark.asyncio
async def test_create_experiment_multiple_files(
    client, sample_folder, default_project_id
):
    files = []
    for f_path in sorted(sample_folder.iterdir()):
        files.append(
            ("files", (f_path.name, open(f_path, "rb"), "application/octet-stream"))
        )

    resp = await client.post(
        "/api/experiments",
        data={
            "project_id": default_project_id,
            "name": "Multi-file Test",
            "description": "Folder upload",
            "instructions": "",
        },
        files=files,
    )

    for _, (_, fh, _) in files:
        fh.close()

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Multi-file Test"
    assert len(body["uploaded_files"]) == 3
    assert body["dataset_ref"].endswith("/")


@pytest.mark.asyncio
async def test_create_experiment_from_s3(client, default_project_id):
    resp = await client.post(
        "/api/experiments/from-s3",
        data={
            "project_id": default_project_id,
            "name": "S3 Test",
            "description": "From S3 bucket",
            "instructions": "Analyze this",
            "s3_path": "s3://datasets/my-data/raw.csv",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "S3 Test"
    assert body["dataset_ref"] == "s3://datasets/my-data/raw.csv"
    assert "session_id" in body


@pytest.mark.asyncio
async def test_get_experiment(client, sample_csv, default_project_id):
    with open(sample_csv, "rb") as f:
        create_resp = await client.post(
            "/api/experiments",
            data={
                "project_id": default_project_id,
                "name": "Get Test",
                "description": "",
                "instructions": "",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    exp_id = create_resp.json()["id"]

    resp = await client.get(f"/api/experiments/{exp_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == exp_id
    assert body["name"] == "Get Test"
    assert len(body["sessions"]) == 1


@pytest.mark.asyncio
async def test_get_experiment_not_found(client):
    resp = await client.get("/api/experiments/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_experiment(client, sample_csv, default_project_id):
    with open(sample_csv, "rb") as f:
        create_resp = await client.post(
            "/api/experiments",
            data={
                "project_id": default_project_id,
                "name": "Delete Me",
                "description": "",
                "instructions": "",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    exp_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/experiments/{exp_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp = await client.get(f"/api/experiments/{exp_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_experiments_after_create(client, sample_csv, default_project_id):
    for i in range(3):
        with open(sample_csv, "rb") as f:
            await client.post(
                "/api/experiments",
                data={
                    "project_id": default_project_id,
                    "name": f"Exp {i}",
                    "description": "",
                    "instructions": "",
                },
                files={"files": ("data.csv", f, "text/csv")},
            )

    resp = await client.get("/api/experiments")
    assert resp.status_code == 200
    # Account for the placeholder "Untitled" experiment auto-created with
    # the test project (see POST /api/projects) plus the 3 explicitly
    # created here.
    assert len(resp.json()) == 4


@pytest.mark.asyncio
async def test_delete_experiment_not_found(client):
    resp = await client.delete("/api/experiments/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_experiment_from_s3_invalid_path(client, default_project_id):
    resp = await client.post(
        "/api/experiments/from-s3",
        data={
            "project_id": default_project_id,
            "name": "Bad S3",
            "description": "",
            "instructions": "",
            "s3_path": "not-an-s3-path",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_experiment_latest_session_tracking(
    client, sample_csv, default_project_id
):
    with open(sample_csv, "rb") as f:
        create_resp = await client.post(
            "/api/experiments",
            data={
                "project_id": default_project_id,
                "name": "Track",
                "description": "",
                "instructions": "",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    exp_id = create_resp.json()["id"]

    # Create a second session
    await client.post(f"/api/experiments/{exp_id}/sessions")

    resp = await client.get("/api/experiments")
    body = resp.json()
    # +1 for the Untitled placeholder auto-created with the project.
    assert len(body) == 2
    tracked = next(e for e in body if e["id"] == exp_id)
    assert tracked["latest_session_id"] is not None
