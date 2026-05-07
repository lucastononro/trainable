"""Agent-declared experiment lifecycle.

Two surfaces:
  - `create_experiment_declared(...)` — create an Experiment under a
    session. Used by the create-experiment skill.
  - `transition_state(...)` — move the experiment through its lifecycle
    (created → prepping → training → trained / failed / abandoned). Used
    by start-training, register-model, and the post-stage cleanup hook.

Both emit SSE events so the frontend can refresh the lineage canvas
without polling.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from db import async_session
from models import (
    Experiment,
    ExperimentDataset,
    ExperimentState,
    Session as SessionModel,
)
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Allowed transitions — keep this aligned with ExperimentState. Anything
# not listed raises ValueError so the agent can't put an experiment in
# an inconsistent state (e.g. trained → training).
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ExperimentState.CREATED.value: {
        ExperimentState.PREPPING.value,
        ExperimentState.TRAINING.value,
        ExperimentState.FAILED.value,
        ExperimentState.ABANDONED.value,
    },
    ExperimentState.PREPPING.value: {
        ExperimentState.CREATED.value,  # revert if prep cleared
        ExperimentState.TRAINING.value,
        ExperimentState.FAILED.value,
        ExperimentState.ABANDONED.value,
    },
    ExperimentState.TRAINING.value: {
        ExperimentState.TRAINED.value,
        ExperimentState.FAILED.value,
        ExperimentState.ABANDONED.value,
    },
    ExperimentState.TRAINED.value: {
        # Re-training the same experiment is rare — typically the agent
        # should create a new experiment. But allow it explicitly.
        ExperimentState.TRAINING.value,
    },
    ExperimentState.FAILED.value: {
        ExperimentState.TRAINING.value,
        ExperimentState.ABANDONED.value,
    },
    ExperimentState.ABANDONED.value: {
        ExperimentState.TRAINING.value,
    },
}


async def create_experiment_declared(
    *,
    session_id: str,
    name: str,
    hypothesis: str,
    description: str = "",
    parent_dataset_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Create a new agent-declared experiment under a session.

    The session must already exist; pass `session_id` from the agent's
    runtime context. Optionally attaches existing DatasetVersions as
    inputs (e.g. a raw upload the agent intends to derive from).
    """
    if not name.strip():
        raise ValueError("name is required for create-experiment")
    if not hypothesis.strip():
        raise ValueError("hypothesis is required (1-3 sentences)")

    async with async_session() as db:
        sess = (
            await db.execute(select(SessionModel).where(SessionModel.id == session_id))
        ).scalar_one_or_none()
        if not sess:
            raise ValueError(f"Session {session_id} not found")

        # Resolve project_id from session, falling back to the legacy
        # session.experiment.project_id link if the session row predates
        # the schema flip.
        project_id = sess.project_id
        if not project_id and sess.experiment_id:
            legacy_exp = (
                await db.execute(
                    select(Experiment.project_id).where(
                        Experiment.id == sess.experiment_id
                    )
                )
            ).scalar_one_or_none()
            project_id = legacy_exp
        if not project_id:
            raise ValueError(
                f"Session {session_id} has no project_id (legacy row?); "
                "supply it before creating experiments."
            )

        eid = str(uuid.uuid4())
        now = _now()
        exp = Experiment(
            id=eid,
            project_id=project_id,
            session_id=session_id,
            name=name.strip(),
            description=description,
            hypothesis=hypothesis.strip(),
            state=ExperimentState.CREATED.value,
            dataset_ref="",
            created_at=now,
            updated_at=now,
        )
        db.add(exp)
        await db.flush()

        if parent_dataset_ids:
            for did in parent_dataset_ids:
                db.add(
                    ExperimentDataset(
                        experiment_id=eid,
                        dataset_version_id=did,
                        role="input",
                    )
                )

        await db.commit()
        await db.refresh(exp)
        result = exp.to_dict()

    # SSE: tell the in-session canvas to refetch lineage.
    try:
        await broadcaster.publish(
            session_id,
            {"type": "experiment_created", "data": result},
        )
    except Exception as e:
        logger.debug("SSE publish for experiment_created skipped: %s", e)
    return result


async def transition_state(
    *,
    experiment_id: str,
    new_state: str,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Move an experiment through its lifecycle.

    Raises ValueError on illegal transitions so the agent gets a clear
    error message in the tool response.
    """
    if new_state not in {s.value for s in ExperimentState}:
        raise ValueError(f"Unknown experiment state: {new_state!r}")

    async with async_session() as db:
        exp = (
            await db.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalar_one_or_none()
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")

        cur = exp.state or ExperimentState.CREATED.value
        if new_state != cur and new_state not in _ALLOWED_TRANSITIONS.get(cur, set()):
            raise ValueError(
                f"Illegal transition {cur!r} → {new_state!r} for experiment "
                f"{experiment_id}. Valid next states: "
                f"{sorted(_ALLOWED_TRANSITIONS.get(cur, set()))}"
            )

        exp.state = new_state
        if started_at:
            exp.started_at = started_at
        if completed_at:
            exp.completed_at = completed_at
        exp.updated_at = _now()
        await db.commit()
        await db.refresh(exp)
        out = exp.to_dict()
        sid = exp.session_id

    if sid:
        try:
            await broadcaster.publish(
                sid,
                {
                    "type": "experiment_state_changed",
                    "data": {
                        "experiment_id": experiment_id,
                        "state": new_state,
                        "started_at": out.get("started_at"),
                        "completed_at": out.get("completed_at"),
                    },
                },
            )
        except Exception as e:
            logger.debug("SSE publish for experiment_state_changed skipped: %s", e)
    return out


async def transition_abandoned_in_session(session_id: str) -> int:
    """Sweep any TRAINING experiments in this session into ABANDONED.

    Called from the post-stage cleanup hook so an agent that crashed or
    forgot to call register-model leaves the experiment row in a sane
    visible state, not stuck on `training` forever.
    """
    abandoned = 0
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(Experiment).where(
                        Experiment.session_id == session_id,
                        Experiment.state == ExperimentState.TRAINING.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            r.state = ExperimentState.ABANDONED.value
            r.completed_at = _now()
            r.updated_at = _now()
            abandoned += 1
        if abandoned:
            await db.commit()

    if abandoned:
        try:
            await broadcaster.publish(
                session_id,
                {
                    "type": "experiments_abandoned",
                    "data": {"session_id": session_id, "count": abandoned},
                },
            )
        except Exception as e:
            logger.debug("SSE publish for experiments_abandoned skipped: %s", e)
    return abandoned


async def get_experiment(experiment_id: str) -> dict | None:
    async with async_session() as db:
        exp = (
            await db.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalar_one_or_none()
        return exp.to_dict() if exp else None
