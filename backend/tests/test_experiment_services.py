"""Service-layer tests: experiment lifecycle, dataset registration, lineage."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db import async_session
from models import (
    Experiment,
    ExperimentDataset,
    ExperimentState,
    Project,
    Session as SessionModel,
)
from services.dataset_versions import (
    record_upload,
    register_dataset_declared,
)
from services.experiments import (
    create_experiment_declared,
    transition_abandoned_in_session,
    transition_state,
)
from services.lineage import (
    build_experiment_lineage,
    build_project_lineage,
    build_session_lineage,
)
from services.registry import register_model_declared


async def _seed_project_session() -> tuple[str, str]:
    pid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Project(id=pid, name="t"))
        db.add(SessionModel(id=sid, project_id=pid))
        await db.commit()
    return pid, sid


@pytest.mark.asyncio
async def test_create_experiment_attaches_to_session():
    pid, sid = await _seed_project_session()
    out = await create_experiment_declared(
        session_id=sid, name="baseline", hypothesis="raw features only"
    )
    assert out["session_id"] == sid
    assert out["state"] == "created"
    assert out["hypothesis"] == "raw features only"
    assert out["project_id"] == pid


@pytest.mark.asyncio
async def test_create_experiment_rejects_blank_hypothesis():
    _, sid = await _seed_project_session()
    with pytest.raises(ValueError, match="hypothesis is required"):
        await create_experiment_declared(session_id=sid, name="x", hypothesis="")


@pytest.mark.asyncio
async def test_create_experiment_rejects_unknown_session():
    with pytest.raises(ValueError, match="not found"):
        await create_experiment_declared(session_id="missing", name="x", hypothesis="y")


@pytest.mark.asyncio
async def test_register_dataset_writes_join_row():
    pid, sid = await _seed_project_session()
    exp = await create_experiment_declared(
        session_id=sid, name="exp", hypothesis="test"
    )
    eid = exp["id"]
    out = await register_dataset_declared(
        experiment_id=eid,
        path="/sessions/x/data/train.parquet",
        name="train splits",
        description="80/10/10 split",
        content_hash="a" * 64,
        size_bytes=1024,
        metadata={"target_column": "y"},
    )
    assert out["kind"] == "processed"
    assert out["description"] == "80/10/10 split"

    async with async_session() as db:
        link = (
            await db.execute(
                select(ExperimentDataset).where(ExperimentDataset.experiment_id == eid)
            )
        ).scalar_one()
        assert link.role == "input"
        assert link.dataset_version_id == out["id"]


@pytest.mark.asyncio
async def test_register_dataset_dedupes_on_hash():
    """Re-registering the same hash returns the existing row, doesn't dupe."""
    pid, sid = await _seed_project_session()
    exp = await create_experiment_declared(
        session_id=sid, name="exp", hypothesis="test"
    )
    eid = exp["id"]

    a = await register_dataset_declared(
        experiment_id=eid,
        path="/x/train.parquet",
        name="t",
        description="d",
        content_hash="b" * 64,
        size_bytes=10,
    )
    b = await register_dataset_declared(
        experiment_id=eid,
        path="/x/train.parquet",
        name="t",
        description="d",
        content_hash="b" * 64,
        size_bytes=10,
    )
    assert a["id"] == b["id"]


@pytest.mark.asyncio
async def test_register_dataset_rejects_blank_description():
    _, sid = await _seed_project_session()
    exp = await create_experiment_declared(
        session_id=sid, name="exp", hypothesis="test"
    )
    with pytest.raises(ValueError, match="description is required"):
        await register_dataset_declared(
            experiment_id=exp["id"],
            path="/x.parquet",
            name="t",
            description="",
            content_hash="c" * 64,
        )


@pytest.mark.asyncio
async def test_record_upload_writes_kind_raw():
    pid = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Project(id=pid, name="t"))
        await db.commit()
    out = await record_upload(
        project_id=pid,
        path=f"/projects/{pid}/datasets/iris.csv",
        content=b"a,b,c\n1,2,3\n",
        name="iris.csv",
        description="user upload",
    )
    assert out["kind"] == "raw"
    assert out["name"] == "iris.csv"
    assert out["source_session_id"] is None
    assert out["source_experiment_id"] is None


@pytest.mark.asyncio
async def test_transition_state_allowed_path():
    _, sid = await _seed_project_session()
    exp = await create_experiment_declared(
        session_id=sid, name="exp", hypothesis="test"
    )
    eid = exp["id"]

    # created → training
    out = await transition_state(
        experiment_id=eid,
        new_state=ExperimentState.TRAINING.value,
        started_at="2026-05-07T00:00:00Z",
    )
    assert out["state"] == ExperimentState.TRAINING.value
    assert out["started_at"] == "2026-05-07T00:00:00Z"

    # training → trained
    out2 = await transition_state(
        experiment_id=eid,
        new_state=ExperimentState.TRAINED.value,
        completed_at="2026-05-07T00:01:00Z",
    )
    assert out2["state"] == ExperimentState.TRAINED.value


@pytest.mark.asyncio
async def test_transition_state_rejects_illegal():
    _, sid = await _seed_project_session()
    exp = await create_experiment_declared(
        session_id=sid, name="exp", hypothesis="test"
    )
    # created → trained is illegal (must go through training)
    with pytest.raises(ValueError, match="Illegal transition"):
        await transition_state(
            experiment_id=exp["id"],
            new_state=ExperimentState.TRAINED.value,
        )


@pytest.mark.asyncio
async def test_register_model_transitions_state_and_creates_row():
    _, sid = await _seed_project_session()
    exp = await create_experiment_declared(
        session_id=sid, name="exp", hypothesis="test"
    )
    eid = exp["id"]

    # The agent declares the lifecycle
    await transition_state(experiment_id=eid, new_state="training")

    # Volume read will fail in tests (no real volume); register-model
    # falls back to using the supplied path as the artifact_uri.
    out = await register_model_declared(
        experiment_id=eid,
        path="/sessions/x/model.pkl",
        framework="xgboost",
        metrics={"accuracy": 0.91},
        description="XGBoost depth=8",
        hyperparams={"max_depth": 8},
    )
    assert out["experiment_id"] == eid
    assert out["version"] == 1
    assert out["description"] == "XGBoost depth=8"
    assert out["metrics_summary"]["accuracy"] == 0.91

    async with async_session() as db:
        e = (
            await db.execute(select(Experiment).where(Experiment.id == eid))
        ).scalar_one()
        assert e.state == ExperimentState.TRAINED.value
        assert e.completed_at is not None


@pytest.mark.asyncio
async def test_register_model_increments_version():
    _, sid = await _seed_project_session()
    e1 = await create_experiment_declared(session_id=sid, name="exp", hypothesis="t")
    e2 = await create_experiment_declared(session_id=sid, name="exp", hypothesis="t")
    await transition_state(experiment_id=e1["id"], new_state="training")
    await transition_state(experiment_id=e2["id"], new_state="training")

    m1 = await register_model_declared(
        experiment_id=e1["id"],
        path="/p/m.pkl",
        framework="xgb",
        metrics={"accuracy": 0.9},
        description="run 1",
        name="iris_xgb",
    )
    m2 = await register_model_declared(
        experiment_id=e2["id"],
        path="/p/m.pkl",
        framework="xgb",
        metrics={"accuracy": 0.91},
        description="run 2",
        name="iris_xgb",
    )
    assert m1["version"] == 1
    assert m2["version"] == 2


@pytest.mark.asyncio
async def test_transition_abandoned_sweeps_training_experiments():
    _, sid = await _seed_project_session()
    e1 = await create_experiment_declared(
        session_id=sid, name="abandoned", hypothesis="t"
    )
    e2 = await create_experiment_declared(session_id=sid, name="ok", hypothesis="t")
    await transition_state(experiment_id=e1["id"], new_state="training")
    # e2 stays in `created`

    n = await transition_abandoned_in_session(sid)
    assert n == 1

    async with async_session() as db:
        rows = (
            (await db.execute(select(Experiment).where(Experiment.session_id == sid)))
            .scalars()
            .all()
        )
        states = {r.id: r.state for r in rows}
    assert states[e1["id"]] == ExperimentState.ABANDONED.value
    assert states[e2["id"]] == ExperimentState.CREATED.value


# ---------------------------------------------------------------------------
# Lineage builders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_lineage_returns_subgraph():
    """Build a small graph: raw → processed → experiment → model."""
    pid, sid = await _seed_project_session()
    raw = await record_upload(
        project_id=pid,
        path="/projects/p/datasets/iris.csv",
        content=b"a,b\n1,2\n",
        name="iris.csv",
    )
    exp = await create_experiment_declared(session_id=sid, name="exp", hypothesis="t")
    eid = exp["id"]
    proc = await register_dataset_declared(
        experiment_id=eid,
        path="/sessions/s/data/train.parquet",
        name="train",
        description="80/10/10",
        content_hash="z" * 64,
        size_bytes=10,
        parent_dataset_id=raw["id"],
    )
    await transition_state(experiment_id=eid, new_state="training")
    await register_model_declared(
        experiment_id=eid,
        path="/sessions/s/model.pkl",
        framework="xgb",
        metrics={"accuracy": 0.9},
        description="iris model",
    )

    g = await build_session_lineage(sid)

    node_types = sorted({n["type"] for n in g["nodes"]})
    assert node_types == ["dataset", "experiment", "model"]
    assert any(n["kind"] == "raw" for n in g["nodes"] if n["type"] == "dataset")
    assert any(n["kind"] == "processed" for n in g["nodes"] if n["type"] == "dataset")

    edge_kinds = sorted({e["kind"] for e in g["edges"]})
    assert edge_kinds == ["derives_from", "feeds", "produces"]

    # raw→processed edge exists
    assert any(
        e["source"] == f"dataset:{raw['id']}" and e["target"] == f"dataset:{proc['id']}"
        for e in g["edges"]
    )


@pytest.mark.asyncio
async def test_project_lineage_includes_orphan_raw_datasets():
    """Raw uploads not yet attached to any experiment still show up as
    starting points in the project graph."""
    pid = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Project(id=pid, name="t"))
        await db.commit()
    raw = await record_upload(
        project_id=pid,
        path="/projects/p/datasets/iris.csv",
        content=b"a,b\n1,2\n",
        name="iris.csv",
    )
    g = await build_project_lineage(pid)
    assert any(n["id"] == f"dataset:{raw['id']}" for n in g["nodes"])
    # No experiments yet → no edges
    assert g["edges"] == []


@pytest.mark.asyncio
async def test_session_lineage_includes_orphan_raw():
    """Raw datasets uploaded to the project must show up in the session
    view even when the agent never linked them via parent_dataset_id."""
    pid, sid = await _seed_project_session()
    raw = await record_upload(
        project_id=pid,
        path=f"/projects/{pid}/datasets/iris.csv",
        content=b"a,b\n1,2\n",
        name="iris.csv",
    )
    # Create an experiment + processed dataset but DO NOT link parent.
    exp = await create_experiment_declared(session_id=sid, name="exp", hypothesis="t")
    await register_dataset_declared(
        experiment_id=exp["id"],
        path="/sessions/x/data/train.parquet",
        name="train",
        description="d",
        content_hash="z" * 64,
        size_bytes=10,
        # parent_dataset_id intentionally omitted
    )
    g = await build_session_lineage(sid)
    raw_node_id = f"dataset:{raw['id']}"
    assert any(n["id"] == raw_node_id for n in g["nodes"]), (
        "Raw dataset should appear in session lineage even without parent linkage"
    )


@pytest.mark.asyncio
async def test_experiment_lineage_walks_parent_chain():
    """Even if only the leaf processed dataset is registered to the
    experiment, the graph should include its raw ancestor via parent_id."""
    pid, sid = await _seed_project_session()
    raw = await record_upload(
        project_id=pid,
        path="/projects/p/datasets/iris.csv",
        content=b"a,b\n1,2\n",
        name="iris.csv",
    )
    exp = await create_experiment_declared(session_id=sid, name="exp", hypothesis="t")
    await register_dataset_declared(
        experiment_id=exp["id"],
        path="/sessions/s/data/train.parquet",
        name="train",
        description="d",
        content_hash="q" * 64,
        size_bytes=10,
        parent_dataset_id=raw["id"],
    )
    g = await build_experiment_lineage(exp["id"])
    raw_node = next((n for n in g["nodes"] if n["id"] == f"dataset:{raw['id']}"), None)
    assert raw_node is not None, "Raw ancestor should be in the experiment subgraph"


@pytest.mark.asyncio
async def test_register_dataset_auto_links_single_raw():
    """When parent_dataset_id is omitted but the project has exactly
    one raw upload, register-dataset should auto-link to it so the
    lineage graph isn't orphaned."""
    pid, sid = await _seed_project_session()
    raw = await record_upload(
        project_id=pid,
        path=f"/projects/{pid}/datasets/iris.csv",
        content=b"a,b\n1,2\n",
        name="iris.csv",
    )
    exp = await create_experiment_declared(session_id=sid, name="exp", hypothesis="t")
    out = await register_dataset_declared(
        experiment_id=exp["id"],
        path="/sessions/x/data/train.parquet",
        name="train",
        description="d",
        content_hash="z" * 64,
        size_bytes=10,
        # parent_dataset_id deliberately omitted
    )
    assert out["parent_id"] == raw["id"], (
        "Expected auto-link to the only raw dataset in the project"
    )


@pytest.mark.asyncio
async def test_register_dataset_no_auto_link_with_multiple_raws():
    """With 2+ raw uploads, the auto-link is intentionally silent —
    require explicit parent_dataset_id."""
    pid, sid = await _seed_project_session()
    await record_upload(
        project_id=pid,
        path=f"/projects/{pid}/datasets/a.csv",
        content=b"a",
        name="a.csv",
    )
    await record_upload(
        project_id=pid,
        path=f"/projects/{pid}/datasets/b.csv",
        content=b"b",
        name="b.csv",
    )
    exp = await create_experiment_declared(session_id=sid, name="exp", hypothesis="t")
    out = await register_dataset_declared(
        experiment_id=exp["id"],
        path="/sessions/x/data/train.parquet",
        name="train",
        description="d",
        content_hash="q" * 64,
        size_bytes=10,
    )
    assert out["parent_id"] is None
