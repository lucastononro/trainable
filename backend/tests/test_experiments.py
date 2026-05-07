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
async def test_delete_experiment_with_usage_events(
    client, sample_csv, default_project_id
):
    """Regression: deleting a chat (experiment) whose sessions emitted
    usage_events used to fail on a FK violation against usage_events.session_id.
    The FK now CASCADEs, and the Session.usage_events relationship cleans up."""
    from sqlalchemy import select

    from db import async_session
    from models import Session as SessionModel, UsageEvent

    with open(sample_csv, "rb") as f:
        create_resp = await client.post(
            "/api/experiments",
            data={
                "project_id": default_project_id,
                "name": "Errored Chat",
                "description": "",
                "instructions": "",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    exp_id = create_resp.json()["id"]

    # Find the auto-created session and seed a usage_events row, mirroring
    # what services.usage.record_llm_usage writes after an LLM call.
    async with async_session() as db:
        sess = (
            await db.execute(
                select(SessionModel).where(SessionModel.experiment_id == exp_id)
            )
        ).scalar_one()
        db.add(
            UsageEvent(
                session_id=sess.id,
                project_id=default_project_id,
                kind="llm",
                provider="openai",
                model="gpt-4o-mini",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                is_error=True,
            )
        )
        await db.commit()
        sess_id = sess.id

    resp = await client.delete(f"/api/experiments/{exp_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True

    # The cascade should have wiped the usage_events row alongside the session.
    async with async_session() as db:
        remaining = (
            (
                await db.execute(
                    select(UsageEvent).where(UsageEvent.session_id == sess_id)
                )
            )
            .scalars()
            .all()
        )
        assert remaining == [], "usage_events not cascaded on session delete"


@pytest.mark.asyncio
async def test_delete_experiment_with_registered_model(
    client, sample_csv, default_project_id
):
    """Regression: deleting an experiment that already has a
    RegisteredModel + RunSnapshot used to fail on FK violation, so the
    user had to click Delete twice. The cascade now wipes both children
    in a single transaction."""
    import uuid as _uuid

    from sqlalchemy import select

    from db import async_session
    from models import (
        Experiment,
        ExperimentState,
        RegisteredModel,
        RunSnapshot,
        Session as SessionModel,
    )

    # Create experiment + session via the legacy route so we have a real
    # FK chain to delete.
    with open(sample_csv, "rb") as f:
        create_resp = await client.post(
            "/api/experiments",
            data={
                "project_id": default_project_id,
                "name": "to-delete",
                "description": "",
                "instructions": "",
            },
            files={"files": ("data.csv", f, "text/csv")},
        )
    exp_id = create_resp.json()["id"]
    sid = create_resp.json()["session_id"]

    # Seed a RegisteredModel + RunSnapshot pointing at this experiment,
    # mirroring what register_model_declared / take_snapshot would write.
    async with async_session() as db:
        # Mark the experiment as trained so the model-row state matches.
        exp = (
            await db.execute(select(Experiment).where(Experiment.id == exp_id))
        ).scalar_one()
        exp.state = ExperimentState.TRAINED.value
        db.add(
            RegisteredModel(
                id=str(_uuid.uuid4()),
                project_id=default_project_id,
                experiment_id=exp_id,
                name="m",
                version=1,
                source_session_id=sid,
                artifact_uri="/x.pkl",
                framework="xgb",
            )
        )
        db.add(
            RunSnapshot(
                experiment_id=exp_id,
                session_id=sid,
                dataset_hash="d" * 64,
                code_hash="c" * 64,
            )
        )
        await db.commit()

    # First delete should succeed in one shot.
    resp = await client.delete(f"/api/experiments/{exp_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True

    # Confirm the cascade actually wiped the children.
    async with async_session() as db:
        m = (
            (
                await db.execute(
                    select(RegisteredModel).where(
                        RegisteredModel.experiment_id == exp_id
                    )
                )
            )
            .scalars()
            .all()
        )
        s = (
            (
                await db.execute(
                    select(RunSnapshot).where(RunSnapshot.experiment_id == exp_id)
                )
            )
            .scalars()
            .all()
        )
        e = (
            await db.execute(select(Experiment).where(Experiment.id == exp_id))
        ).scalar_one_or_none()
        sess = (
            await db.execute(select(SessionModel).where(SessionModel.id == sid))
        ).scalar_one_or_none()
    assert m == []
    assert s == []
    assert e is None
    # Legacy session was a child via Experiment.sessions cascade — also gone.
    assert sess is None


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
