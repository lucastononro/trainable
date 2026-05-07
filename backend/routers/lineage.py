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
    """Returns the experiment row plus its linked datasets, model, and
    snapshot — everything the standalone /experiments/[id] detail page
    needs without N round-trips."""
    from sqlalchemy import select

    from db import async_session
    from models import (
        DatasetVersion,
        ExperimentDataset,
        RegisteredModel,
        RunSnapshot,
    )

    exp = await get_experiment(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")

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

    return {
        **exp,
        "datasets": datasets,
        "model": model.to_dict() if model else None,
        "snapshot": snap.to_dict() if snap else None,
    }
