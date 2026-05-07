"""Lineage + dataset endpoints for the in-session canvas tab and the
standalone /projects/{id}/lineage page.

All four routes return data shaped for direct consumption by React Flow
(nodes/edges arrays). The Sidebar tree consumes the dataset + session
list endpoints to render the new Project → Session → Experiment hierarchy.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.experiments import get_experiment
from services.lineage import (
    build_experiment_lineage,
    build_project_lineage,
    build_session_lineage,
    get_dataset,
    list_project_datasets,
    list_project_sessions,
    list_session_experiments,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Lineage graphs
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/lineage")
async def project_lineage(project_id: str):
    return await build_project_lineage(project_id)


@router.get("/sessions/{session_id}/lineage")
async def session_lineage(session_id: str):
    return await build_session_lineage(session_id)


@router.get("/experiments/{experiment_id}/lineage")
async def experiment_lineage(experiment_id: str):
    return await build_experiment_lineage(experiment_id)


# ---------------------------------------------------------------------------
# Dataset detail (for the metadata side-panel + project-level browser)
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/datasets")
async def project_datasets(project_id: str):
    return await list_project_datasets(project_id)


@router.get("/datasets/{dataset_id}")
async def dataset_detail(dataset_id: int):
    row = await get_dataset(dataset_id)
    if not row:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return row


# ---------------------------------------------------------------------------
# Sidebar tree helpers (Project → Session → Experiment)
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/sessions")
async def project_sessions(project_id: str):
    return await list_project_sessions(project_id)


@router.get("/sessions/{session_id}/experiments")
async def session_experiments(session_id: str):
    return await list_session_experiments(session_id)


# ---------------------------------------------------------------------------
# Experiment detail — fuller payload than experiments.py for the new page
# ---------------------------------------------------------------------------


@router.get("/experiments/{experiment_id}/detail")
async def experiment_detail(experiment_id: str):
    """Returns the experiment row plus its linked datasets, model,
    snapshot, and the sessions it touches.

    Sessions are resolved across both directions of the cardinality flip:
      - the canonical Experiment.session_id (post-flip)
      - the legacy 1:N Session.experiment_id (pre-flip)
    deduped by id and ordered by created_at ascending so the user sees
    the original session first.
    """
    from sqlalchemy import or_, select

    from db import async_session
    from models import (
        DatasetVersion,
        ExperimentDataset,
        RegisteredModel,
        RunSnapshot,
        Session as SessionModel,
    )

    exp = await get_experiment(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")

    canonical_session_id = exp.get("session_id")

    async with async_session() as db:
        link_rows = (
            await db.execute(
                select(ExperimentDataset, DatasetVersion)
                .join(
                    DatasetVersion,
                    DatasetVersion.id == ExperimentDataset.dataset_version_id,
                )
                .where(ExperimentDataset.experiment_id == experiment_id)
            )
        ).all()
        datasets = [{**dv.to_dict(), "role": link.role} for (link, dv) in link_rows]

        model = (
            await db.execute(
                select(RegisteredModel).where(
                    RegisteredModel.experiment_id == experiment_id
                )
            )
        ).scalar_one_or_none()

        snap = (
            await db.execute(
                select(RunSnapshot).where(RunSnapshot.experiment_id == experiment_id)
            )
        ).scalar_one_or_none()

        # Sessions linked via either direction. Without the OR, agent-
        # declared experiments (where Session.experiment_id is unset)
        # return [] and the page shows "No sessions" even though the
        # canonical pointer is right on the experiment row itself.
        session_filter = SessionModel.experiment_id == experiment_id
        if canonical_session_id:
            session_filter = or_(
                session_filter, SessionModel.id == canonical_session_id
            )
        session_rows = (
            (
                await db.execute(
                    select(SessionModel)
                    .where(session_filter)
                    .order_by(SessionModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        seen_session_ids = set()
        sessions = []
        for s in session_rows:
            if s.id in seen_session_ids:
                continue
            seen_session_ids.add(s.id)
            sessions.append(s.to_dict())

    return {
        **exp,
        "datasets": datasets,
        "model": model.to_dict() if model else None,
        "snapshot": snap.to_dict() if snap else None,
        "sessions": sessions,
    }
