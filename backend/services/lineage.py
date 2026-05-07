"""Lineage graph builders.

Produce {nodes, edges} payloads the frontend's React Flow component can
render directly. Three scopes:
  - per-experiment: just this experiment's raw → processed → model chain
  - per-session: all experiments in this session, sharing dataset nodes
    where they branch from the same parent
  - per-project: aggregate across all sessions
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from db import async_session
from models import (
    DatasetVersion,
    Experiment,
    ExperimentDataset,
    RegisteredModel,
    Session as SessionModel,
)

logger = logging.getLogger(__name__)


def _dataset_node(dv: DatasetVersion) -> dict[str, Any]:
    return {
        "id": f"dataset:{dv.id}",
        "type": "dataset",
        "kind": dv.kind or "raw",
        "name": dv.name or dv.path.rsplit("/", 1)[-1],
        "description": dv.description or "",
        "path": dv.path,
        "size_bytes": dv.size_bytes or 0,
        "hash": dv.hash,
        "source_session_id": dv.source_session_id,
        "source_experiment_id": dv.source_experiment_id,
        "metadata": dv.dataset_metadata or {},
        "created_at": dv.created_at,
    }


def _model_node(m: RegisteredModel) -> dict[str, Any]:
    return {
        "id": f"model:{m.id}",
        "type": "model",
        "name": f"{m.name} v{m.version}",
        "model_id": m.id,
        "experiment_id": m.experiment_id,
        "description": m.description or "",
        "framework": m.framework or "",
        "metrics_summary": m.metrics_summary or {},
        "hyperparams": m.hyperparams or {},
        "version": m.version,
        "created_at": m.created_at,
    }


def _experiment_node(e: Experiment) -> dict[str, Any]:
    return {
        "id": f"experiment:{e.id}",
        "type": "experiment",
        "name": e.name,
        "experiment_id": e.id,
        "session_id": e.session_id,
        "hypothesis": e.hypothesis or "",
        "state": e.state or "created",
        "started_at": e.started_at,
        "completed_at": e.completed_at,
        "created_at": e.created_at,
    }


async def _collect_dataset_chain(db, root_ids: set[int]) -> dict[int, DatasetVersion]:
    """Walk parent_id from every root toward ancestors so the graph
    includes Raw → Processed lineage even if only the leaf was requested."""
    seen: dict[int, DatasetVersion] = {}
    frontier = set(root_ids)
    while frontier:
        rows = (
            (
                await db.execute(
                    select(DatasetVersion).where(DatasetVersion.id.in_(frontier))
                )
            )
            .scalars()
            .all()
        )
        new_frontier = set()
        for r in rows:
            if r.id not in seen:
                seen[r.id] = r
                if r.parent_id and r.parent_id not in seen:
                    new_frontier.add(r.parent_id)
        frontier = new_frontier
    return seen


async def _build_for_experiments(experiment_ids: list[str]) -> dict[str, Any]:
    """Shared core: build {nodes, edges} for a set of experiments."""
    if not experiment_ids:
        return {"nodes": [], "edges": []}

    async with async_session() as db:
        experiments = (
            (
                await db.execute(
                    select(Experiment).where(Experiment.id.in_(experiment_ids))
                )
            )
            .scalars()
            .all()
        )
        links = (
            (
                await db.execute(
                    select(ExperimentDataset).where(
                        ExperimentDataset.experiment_id.in_(experiment_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        models = (
            (
                await db.execute(
                    select(RegisteredModel).where(
                        RegisteredModel.experiment_id.in_(experiment_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

        leaf_dataset_ids = {link.dataset_version_id for link in links}
        datasets = await _collect_dataset_chain(db, leaf_dataset_ids)

    # Build node + edge lists. Nodes are deduped by id.
    nodes: list[dict] = []
    seen_ids: set[str] = set()

    def _add(n: dict):
        if n["id"] in seen_ids:
            return
        seen_ids.add(n["id"])
        nodes.append(n)

    for dv in datasets.values():
        _add(_dataset_node(dv))
    for e in experiments:
        _add(_experiment_node(e))
    for m in models:
        _add(_model_node(m))

    edges: list[dict] = []

    # parent_id chains within datasets
    for dv in datasets.values():
        if dv.parent_id and dv.parent_id in datasets:
            edges.append(
                {
                    "id": f"e_dv_{dv.parent_id}_to_{dv.id}",
                    "source": f"dataset:{dv.parent_id}",
                    "target": f"dataset:{dv.id}",
                    "kind": "derives_from",
                }
            )

    # dataset → experiment via ExperimentDataset(role='input')
    for link in links:
        if link.role != "input":
            continue
        edges.append(
            {
                "id": f"e_link_{link.id}",
                "source": f"dataset:{link.dataset_version_id}",
                "target": f"experiment:{link.experiment_id}",
                "kind": "feeds",
            }
        )

    # experiment → model
    for m in models:
        edges.append(
            {
                "id": f"e_exp_to_model_{m.id}",
                "source": f"experiment:{m.experiment_id}",
                "target": f"model:{m.id}",
                "kind": "produces",
            }
        )

    return {"nodes": nodes, "edges": edges}


async def build_experiment_lineage(experiment_id: str) -> dict[str, Any]:
    return await _build_for_experiments([experiment_id])


async def build_session_lineage(session_id: str) -> dict[str, Any]:
    async with async_session() as db:
        ids = (
            (
                await db.execute(
                    select(Experiment.id).where(Experiment.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
    return await _build_for_experiments(list(ids))


async def build_project_lineage(project_id: str) -> dict[str, Any]:
    async with async_session() as db:
        # All experiments in the project (including legacy ones via
        # project_id, even if they don't have session_id set yet).
        exp_ids = (
            (
                await db.execute(
                    select(Experiment.id).where(Experiment.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        # Plus any orphan raw datasets the user uploaded to the project
        # that aren't yet attached to any experiment — they should still
        # show up as starting points in the graph.
        raw_only = (
            (
                await db.execute(
                    select(DatasetVersion).where(
                        DatasetVersion.project_id == project_id,
                        DatasetVersion.kind == "raw",
                    )
                )
            )
            .scalars()
            .all()
        )

    base = await _build_for_experiments(list(exp_ids))
    seen = {n["id"] for n in base["nodes"]}
    for dv in raw_only:
        nid = f"dataset:{dv.id}"
        if nid not in seen:
            base["nodes"].append(_dataset_node(dv))
            seen.add(nid)
    return base


async def list_project_datasets(project_id: str) -> list[dict]:
    """Flat list of all DatasetVersions in a project — used by the
    project-level dataset browser and the metadata side panel."""
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(DatasetVersion)
                    .where(DatasetVersion.project_id == project_id)
                    .order_by(DatasetVersion.id.desc())
                )
            )
            .scalars()
            .all()
        )
        return [r.to_dict() for r in rows]


async def get_dataset(dataset_id: int) -> dict | None:
    async with async_session() as db:
        row = (
            await db.execute(
                select(DatasetVersion).where(DatasetVersion.id == dataset_id)
            )
        ).scalar_one_or_none()
        return row.to_dict() if row else None


async def list_session_experiments(session_id: str) -> list[dict]:
    """Helper for the sidebar tree and lineage views."""
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(Experiment)
                    .where(Experiment.session_id == session_id)
                    .order_by(Experiment.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [r.to_dict() for r in rows]


async def list_project_sessions(project_id: str) -> list[dict]:
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(SessionModel)
                    .where(SessionModel.project_id == project_id)
                    .order_by(SessionModel.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [r.to_dict() for r in rows]
