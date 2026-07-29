"""Validator state-machine tests — the new gates that flag
experiments in TRAINING but never closed via register-model.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db import async_session
from models import Experiment, ExperimentState, Project, Session as SessionModel
from services.experiments import (
    create_experiment_declared,
    transition_abandoned_in_session,
    transition_state,
)
from services.registry import register_model_declared
from services.validator import validate_train_output


async def _seed() -> tuple[str, str]:
    pid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Project(id=pid, name="t"))
        db.add(SessionModel(id=sid, project_id=pid))
        await db.commit()
    return pid, sid


@pytest.mark.asyncio
async def test_validate_train_flags_training_state_with_no_register_model():
    """Experiment opened with start-training but never closed → CRITICAL error."""
    _, sid = await _seed()
    exp = await create_experiment_declared(
        session_id=sid, name="abandoned_xgb", hypothesis="t"
    )
    await transition_state(experiment_id=exp["id"], new_state="training")

    res = await validate_train_output(sid, exp["id"])
    critical = [e for e in res["errors"] if "start-training" in e]
    assert critical, f"Expected CRITICAL error about missing register-model: {res}"


@pytest.mark.asyncio
async def test_validate_train_passes_when_model_registered():
    pid, sid = await _seed()
    exp = await create_experiment_declared(session_id=sid, name="ok", hypothesis="t")
    # Seed a processed dataset so register-model has a valid training_dataset_id.
    from datetime import datetime, timezone
    from models import DatasetVersion

    async with async_session() as db:
        dv = DatasetVersion(
            project_id=pid,
            kind="processed",
            name="proc",
            description="seeded",
            hash="p" * 64,
            path="/p/proc.parquet",
            size_bytes=64,
            source_experiment_id=exp["id"],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(dv)
        await db.commit()
        await db.refresh(dv)
        proc_id = dv.id

    await transition_state(experiment_id=exp["id"], new_state="training")
    await register_model_declared(
        experiment_id=exp["id"],
        path="/sessions/x/model.pkl",
        framework="xgb",
        metrics={"accuracy": 0.9},
        description="test",
        training_dataset_id=proc_id,
    )

    res = await validate_train_output(sid, exp["id"])
    # No lifecycle errors — only legacy file-walk warnings (model file
    # doesn't actually exist on the volume in tests).
    lifecycle_errors = [e for e in res["errors"] if "start-training" in e]
    assert lifecycle_errors == []
    passed_msgs = [p for p in res["passed"] if "Model registered" in p]
    assert passed_msgs, f"Expected lifecycle pass message in {res['passed']}"


@pytest.mark.asyncio
async def test_validate_train_warns_on_created_only_experiment():
    """Experiment opened but never started → soft warning, not critical."""
    _, sid = await _seed()
    exp = await create_experiment_declared(session_id=sid, name="x", hypothesis="t")
    res = await validate_train_output(sid, exp["id"])
    warns = [w for w in res["warnings"] if "no training run was started" in w]
    assert warns


@pytest.mark.asyncio
async def test_validate_train_silent_when_no_experiments():
    """Pre-flip / non-agent code paths still go through the legacy
    file-walk branch without lifecycle errors."""
    _, sid = await _seed()
    res = await validate_train_output(sid, "no-exp-id")
    lifecycle_errors = [e for e in res["errors"] if "start-training" in e]
    assert lifecycle_errors == []


@pytest.mark.asyncio
async def test_abandoned_cleanup_transitions_only_in_session():
    """The cleanup hook must scope to the session — don't sweep
    in-flight experiments in *other* sessions."""
    _, sid_a = await _seed()
    _, sid_b = await _seed()
    e_a = await create_experiment_declared(session_id=sid_a, name="a", hypothesis="t")
    e_b = await create_experiment_declared(session_id=sid_b, name="b", hypothesis="t")
    await transition_state(experiment_id=e_a["id"], new_state="training")
    await transition_state(experiment_id=e_b["id"], new_state="training")

    n = await transition_abandoned_in_session(sid_a)
    assert n == 1

    async with async_session() as db:
        rows = (await db.execute(select(Experiment))).scalars().all()
        states = {r.id: r.state for r in rows}
    assert states[e_a["id"]] == ExperimentState.ABANDONED.value
    assert states[e_b["id"]] == ExperimentState.TRAINING.value
