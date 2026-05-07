"""HTTP smoke tests for the lineage routes."""

from __future__ import annotations

import uuid

import pytest

from db import async_session
from models import Project, Session as SessionModel
from services.dataset_versions import record_upload, register_dataset_declared
from services.experiments import create_experiment_declared, transition_state
from services.registry import register_model_declared


async def _seed() -> tuple[str, str]:
    pid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Project(id=pid, name="t"))
        db.add(SessionModel(id=sid, project_id=pid))
        await db.commit()
    return pid, sid


@pytest.mark.asyncio
async def test_session_lineage_endpoint(client):
    pid, sid = await _seed()
    raw = await record_upload(
        project_id=pid,
        path=f"/projects/{pid}/datasets/iris.csv",
        content=b"a,b\n1,2\n",
        name="iris.csv",
    )
    exp = await create_experiment_declared(session_id=sid, name="exp", hypothesis="t")
    await register_dataset_declared(
        experiment_id=exp["id"],
        path="/sessions/x/data/train.parquet",
        name="train",
        description="d",
        content_hash="z" * 64,
        size_bytes=10,
        parent_dataset_id=raw["id"],
    )
    await transition_state(experiment_id=exp["id"], new_state="training")
    await register_model_declared(
        experiment_id=exp["id"],
        path="/sessions/x/model.pkl",
        framework="xgb",
        metrics={"accuracy": 0.9},
        description="m",
    )

    resp = await client.get(f"/api/sessions/{sid}/lineage")
    assert resp.status_code == 200
    body = resp.json()
    types = sorted({n["type"] for n in body["nodes"]})
    # Experiments are no longer rendered as nodes in the lineage graph.
    assert types == ["dataset", "model"]


@pytest.mark.asyncio
async def test_project_lineage_endpoint(client):
    pid, _ = await _seed()
    await record_upload(
        project_id=pid,
        path=f"/projects/{pid}/datasets/iris.csv",
        content=b"a,b\n1,2\n",
        name="iris.csv",
    )
    resp = await client.get(f"/api/projects/{pid}/lineage")
    assert resp.status_code == 200
    body = resp.json()
    assert any(n["type"] == "dataset" and n["kind"] == "raw" for n in body["nodes"])


@pytest.mark.asyncio
async def test_experiment_detail_endpoint(client):
    pid, sid = await _seed()
    exp = await create_experiment_declared(session_id=sid, name="exp", hypothesis="hyp")
    resp = await client.get(f"/api/experiments/{exp['id']}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == exp["id"]
    assert body["hypothesis"] == "hyp"
    assert body["datasets"] == []
    assert body["model"] is None
    assert body["snapshot"] is None


@pytest.mark.asyncio
async def test_experiment_detail_includes_sessions(client):
    """Detail payload must surface every session linked to the experiment
    (canonical Experiment.session_id + legacy Session.experiment_id),
    deduped."""
    pid, sid = await _seed()
    exp = await create_experiment_declared(session_id=sid, name="exp", hypothesis="hyp")

    # Add a legacy-direction session pointing at the same experiment so
    # we exercise the dedupe-by-id path.
    legacy_sid = str(uuid.uuid4())
    async with async_session() as db:
        db.add(SessionModel(id=legacy_sid, project_id=pid, experiment_id=exp["id"]))
        await db.commit()

    resp = await client.get(f"/api/experiments/{exp['id']}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert "sessions" in body
    ids = sorted(s["id"] for s in body["sessions"])
    assert sorted({sid, legacy_sid}) == ids


@pytest.mark.asyncio
async def test_dataset_detail_404(client):
    resp = await client.get("/api/datasets/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_project_datasets_lists_uploads(client):
    pid, _ = await _seed()
    await record_upload(
        project_id=pid,
        path=f"/projects/{pid}/datasets/x.csv",
        content=b"x",
        name="x.csv",
    )
    resp = await client.get(f"/api/projects/{pid}/datasets")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["kind"] == "raw"
