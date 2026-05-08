"""Schema-flip regression tests for the agent-declared experiments redesign.

These cover the new cardinality (Session ─< Experiment) and the new columns
(state, hypothesis, started_at/completed_at, ExperimentDataset, etc.) without
yet asserting agent-skill behavior — that lands in test_experiment_skills.py
once the service layer + skill handlers are wired up.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db import async_session
from models import (
    DatasetVersion,
    Experiment,
    ExperimentDataset,
    ExperimentState,
    Project,
    RegisteredModel,
    RunSnapshot,
    Session as SessionModel,
)


async def _make_project(db) -> str:
    pid = str(uuid.uuid4())
    db.add(Project(id=pid, name="t"))
    await db.commit()
    return pid


@pytest.mark.asyncio
async def test_session_can_hold_multiple_experiments():
    """The flipped cardinality: one chat session can declare N experiments."""
    async with async_session() as db:
        pid = await _make_project(db)
        sid = str(uuid.uuid4())
        db.add(SessionModel(id=sid, project_id=pid))
        await db.commit()

        for name in ("baseline", "feature_eng", "xgb_tuned"):
            db.add(
                Experiment(
                    id=str(uuid.uuid4()),
                    project_id=pid,
                    session_id=sid,
                    name=name,
                    dataset_ref="",
                    state=ExperimentState.CREATED.value,
                )
            )
        await db.commit()

        rows = (
            (await db.execute(select(Experiment).where(Experiment.session_id == sid)))
            .scalars()
            .all()
        )
        assert {r.name for r in rows} == {"baseline", "feature_eng", "xgb_tuned"}


@pytest.mark.asyncio
async def test_experiment_state_transitions_persist():
    """Lifecycle column is a plain VARCHAR; we read/write the enum's value."""
    async with async_session() as db:
        pid = await _make_project(db)
        sid = str(uuid.uuid4())
        db.add(SessionModel(id=sid, project_id=pid))
        eid = str(uuid.uuid4())
        db.add(
            Experiment(
                id=eid,
                project_id=pid,
                session_id=sid,
                name="exp",
                dataset_ref="",
                state=ExperimentState.CREATED.value,
            )
        )
        await db.commit()

    # Round-trip TRAINED back via a fresh session to prove it persists.
    async with async_session() as db:
        exp = (
            await db.execute(select(Experiment).where(Experiment.id == eid))
        ).scalar_one()
        exp.state = ExperimentState.TRAINING.value
        exp.started_at = "2026-05-07T00:00:00Z"
        await db.commit()

    async with async_session() as db:
        exp = (
            await db.execute(select(Experiment).where(Experiment.id == eid))
        ).scalar_one()
        assert exp.state == ExperimentState.TRAINING.value
        assert exp.started_at == "2026-05-07T00:00:00Z"


@pytest.mark.asyncio
async def test_dataset_version_kind_and_parent_chain():
    """Raw → processed-v1 → processed-v2 lineage via parent_id self-FK."""
    async with async_session() as db:
        pid = await _make_project(db)
        raw = DatasetVersion(
            project_id=pid,
            kind="raw",
            name="iris.csv",
            description="user upload",
            hash="a" * 64,
            path="/projects/p/datasets/iris.csv",
            size_bytes=1024,
        )
        db.add(raw)
        await db.commit()
        await db.refresh(raw)

        proc1 = DatasetVersion(
            project_id=pid,
            kind="processed",
            name="train splits",
            description="80/10/10 split, scaled numerics",
            hash="b" * 64,
            path="/sessions/s1/data/train.parquet",
            parent_id=raw.id,
            size_bytes=512,
            dataset_metadata={"target_column": "species", "train_rows": 105},
        )
        db.add(proc1)
        await db.commit()
        await db.refresh(proc1)

    async with async_session() as db:
        children = (
            (
                await db.execute(
                    select(DatasetVersion).where(DatasetVersion.parent_id == raw.id)
                )
            )
            .scalars()
            .all()
        )
        assert [c.kind for c in children] == ["processed"]
        assert children[0].dataset_metadata["target_column"] == "species"


@pytest.mark.asyncio
async def test_experiment_dataset_join_table_records_role():
    async with async_session() as db:
        pid = await _make_project(db)
        sid = str(uuid.uuid4())
        db.add(SessionModel(id=sid, project_id=pid))
        eid = str(uuid.uuid4())
        db.add(
            Experiment(
                id=eid,
                project_id=pid,
                session_id=sid,
                name="exp",
                dataset_ref="",
                state=ExperimentState.CREATED.value,
            )
        )
        dv = DatasetVersion(
            project_id=pid,
            kind="processed",
            name="train",
            hash="c" * 64,
            path="/sessions/s/data/train.parquet",
        )
        db.add(dv)
        await db.commit()
        await db.refresh(dv)

        db.add(
            ExperimentDataset(experiment_id=eid, dataset_version_id=dv.id, role="input")
        )
        await db.commit()

    async with async_session() as db:
        link = (
            await db.execute(
                select(ExperimentDataset).where(ExperimentDataset.experiment_id == eid)
            )
        ).scalar_one()
        assert link.role == "input"
        assert link.dataset_version_id == dv.id


@pytest.mark.asyncio
async def test_registered_model_links_to_experiment():
    """The new canonical link: a model belongs to one experiment."""
    async with async_session() as db:
        pid = await _make_project(db)
        sid = str(uuid.uuid4())
        db.add(SessionModel(id=sid, project_id=pid))
        eid = str(uuid.uuid4())
        db.add(
            Experiment(
                id=eid,
                project_id=pid,
                session_id=sid,
                name="exp",
                dataset_ref="",
                state=ExperimentState.TRAINED.value,
            )
        )
        await db.commit()

        mid = str(uuid.uuid4())
        db.add(
            RegisteredModel(
                id=mid,
                project_id=pid,
                experiment_id=eid,
                name="iris_xgb",
                version=1,
                source_session_id=sid,
                artifact_uri="/projects/p/models/iris_xgb/v1/model.pkl",
                description="XGBoost with depth=8",
                hyperparams={"max_depth": 8, "n_estimators": 300},
                framework="xgboost",
            )
        )
        await db.commit()

    async with async_session() as db:
        m = (
            await db.execute(select(RegisteredModel).where(RegisteredModel.id == mid))
        ).scalar_one()
        assert m.experiment_id == eid
        assert m.description == "XGBoost with depth=8"
        assert m.hyperparams["max_depth"] == 8


@pytest.mark.asyncio
async def test_run_snapshot_keys_on_experiment():
    """One snapshot per experiment under the new schema."""
    async with async_session() as db:
        pid = await _make_project(db)
        sid = str(uuid.uuid4())
        db.add(SessionModel(id=sid, project_id=pid))
        eid = str(uuid.uuid4())
        db.add(
            Experiment(
                id=eid,
                project_id=pid,
                session_id=sid,
                name="exp",
                dataset_ref="",
                state=ExperimentState.TRAINED.value,
            )
        )
        await db.commit()

        snap = RunSnapshot(
            experiment_id=eid,
            session_id=sid,
            dataset_hash="d" * 64,
            code_hash="e" * 64,
            hyperparams={"lr": 0.01},
            manifest_uri="/sessions/s/snapshot.json",
        )
        db.add(snap)
        await db.commit()
        await db.refresh(snap)

    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(RunSnapshot).where(RunSnapshot.experiment_id == eid)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].dataset_hash == "d" * 64


@pytest.mark.asyncio
async def test_legacy_session_with_experiment_id_still_inserts():
    """Back-compat: legacy POST /api/experiments code creates Experiment +
    Session(experiment_id=X) in one commit. After the schema flip this
    must still work — the cycle between the new Experiment.session_id and
    the legacy Session.experiment_id is broken by keeping the new side
    as a non-FK column + viewonly relationship."""
    async with async_session() as db:
        pid = await _make_project(db)
        eid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        # Legacy creation order: experiment first, then session pointing
        # back to it.
        db.add(
            Experiment(
                id=eid,
                project_id=pid,
                name="legacy",
                dataset_ref="s3://x",
                state=ExperimentState.CREATED.value,
            )
        )
        db.add(SessionModel(id=sid, experiment_id=eid))
        await db.commit()

    async with async_session() as db:
        sess = (
            await db.execute(select(SessionModel).where(SessionModel.id == sid))
        ).scalar_one()
        assert sess.experiment_id == eid
