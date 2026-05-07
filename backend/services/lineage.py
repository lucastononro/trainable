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
        # We don't render experiment-type nodes anymore (the layout is
        # Data → Models with the experiment implicit), so we only need
        # the input-dataset links + the registered models.
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

    # Experiments are NOT rendered as nodes — the user's reference
    # screenshot shows just data → models, with the experiment being
    # the implicit context (each model carries experiment_id). Keeping
    # them as nodes was visual noise.
    for dv in datasets.values():
        _add(_dataset_node(dv))
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

    # Direct dataset → model edges via ExperimentDataset(role='input').
    # If experiment X has model M and input datasets D1, D2 then we emit
    # M←D1 and M←D2. Without the experiment node in between, the graph
    # reads as "this model came from this data."
    inputs_per_experiment: dict[str, list[int]] = {}
    for link in links:
        if link.role != "input":
            continue
        inputs_per_experiment.setdefault(link.experiment_id, []).append(
            link.dataset_version_id
        )
    for m in models:
        for dv_id in inputs_per_experiment.get(m.experiment_id, []):
            edges.append(
                {
                    "id": f"e_dv{dv_id}_to_model_{m.id}",
                    "source": f"dataset:{dv_id}",
                    "target": f"model:{m.id}",
                    "kind": "trained_into",
                    "experiment_id": m.experiment_id,
                }
            )

    return {"nodes": nodes, "edges": edges}


async def _attach_project_raw(
    base: dict[str, Any], project_id: str | None
) -> dict[str, Any]:
    """Augment a {nodes, edges} payload with the project's raw datasets.

    Without this, session- and experiment-scoped graphs omit raw data
    whenever the agent forgot to set `parent_dataset_id` on the
    register-dataset call — which is the common case during early use.
    Raw datasets are project-scoped, so we always attach them as
    starting points; missing edges to processed datasets just mean
    "lineage was never declared."
    """
    if not project_id:
        return base
    async with async_session() as db:
        rows = (
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
    seen = {n["id"] for n in base["nodes"]}
    for dv in rows:
        nid = f"dataset:{dv.id}"
        if nid not in seen:
            base["nodes"].append(_dataset_node(dv))
            seen.add(nid)
    return base


async def build_experiment_lineage(experiment_id: str) -> dict[str, Any]:
    base = await _build_for_experiments([experiment_id])
    async with async_session() as db:
        pid = (
            await db.execute(
                select(Experiment.project_id).where(Experiment.id == experiment_id)
            )
        ).scalar_one_or_none()
    return await _attach_project_raw(base, pid)


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
        sess = (
            await db.execute(
                select(SessionModel.project_id, SessionModel.experiment_id).where(
                    SessionModel.id == session_id
                )
            )
        ).one_or_none()
        # Resolve project_id, falling back to the legacy session→experiment
        # join for sessions that predate the schema flip.
        pid = sess[0] if sess else None
        if not pid and sess and sess[1]:
            pid = (
                await db.execute(
                    select(Experiment.project_id).where(Experiment.id == sess[1])
                )
            ).scalar_one_or_none()
    base = await _build_for_experiments(list(ids))
    return await _attach_project_raw(base, pid)


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
    base = await _build_for_experiments(list(exp_ids))
    return await _attach_project_raw(base, project_id)


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
