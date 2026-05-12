"""Model registry — promote a session-output pickle into a stable artifact.

Sessions die; promoted models don't. `promote_session_model` finds the most
recent model artifact in a session workspace, copies it to a project-level
path, and writes a `RegisteredModel` row.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import desc, select

from db import async_session
from models import (
    Artifact,
    Experiment,
    Metric,
    RegisteredModel,
    Session as SessionModel,
)
from services.volume import (
    listdir_async,
    read_volume_file_async,
    reload_volume_async,
    write_to_volume,
)

logger = logging.getLogger(__name__)

_MODEL_EXTS = (".pkl", ".joblib", ".pt", ".h5", ".onnx", ".keras", ".bin")


def _detect_framework(path: str) -> str | None:
    name = path.lower()
    if name.endswith((".pkl", ".joblib")):
        return "sklearn"
    if name.endswith((".pt",)):
        return "pytorch"
    if name.endswith((".h5", ".keras")):
        return "tensorflow"
    if name.endswith(".onnx"):
        return "onnx"
    if name.endswith(".bin"):
        return "huggingface"
    return None


async def _find_model_artifact(session_id: str) -> dict | None:
    """Return the most recently modified model file under the session workspace.

    Prefers files at the canonical `model.pkl` path, falls back to anything
    matching `_MODEL_EXTS` ordered by mtime.
    """
    workspace = f"/sessions/{session_id}"
    await reload_volume_async()
    try:
        entries = await listdir_async(workspace, recursive=True)
    except FileNotFoundError:
        return None

    best: dict | None = None
    for entry in entries:
        if entry.type.name != "FILE":
            continue
        path = entry.path
        if not path.lower().endswith(_MODEL_EXTS):
            continue
        rel = path[len(workspace) + 1 :] if path.startswith(workspace + "/") else path
        size = int(getattr(entry, "size", 0) or 0)
        mtime = float(getattr(entry, "mtime", 0) or 0)
        priority = 1 if rel == "model.pkl" else 0
        record = {
            "path": path,
            "rel": rel,
            "size": size,
            "mtime": mtime,
            "priority": priority,
        }
        if best is None or (record["priority"], record["mtime"]) > (
            best["priority"],
            best["mtime"],
        ):
            best = record
    return best


async def _summarize_metrics(db, session_id: str) -> dict[str, float]:
    """Pull the final value for each (stage, name) metric reported in the session."""
    rows = (
        (
            await db.execute(
                select(Metric)
                .where(Metric.session_id == session_id)
                .order_by(Metric.id.desc())
                .limit(2000)
            )
        )
        .scalars()
        .all()
    )

    seen: dict[str, float] = {}
    for m in rows:
        key = f"{m.stage}.{m.name}" if m.stage else m.name
        if key not in seen:
            seen[key] = float(m.value)
    return seen


async def promote_session_model(
    *,
    session_id: str,
    name: str | None = None,
) -> dict[str, Any]:
    """Copy the session's model artifact into the registry.

    Returns the to_dict() of the new RegisteredModel row. Raises ValueError
    if the session has no model artifact.
    """
    artifact = await _find_model_artifact(session_id)
    if not artifact:
        raise ValueError(f"No model artifact found in session {session_id}")

    async with async_session() as db:
        # Resolve project_id via the session → experiment join.
        proj_row = (
            await db.execute(
                select(Experiment.project_id, Experiment.name)
                .join(SessionModel, SessionModel.experiment_id == Experiment.id)
                .where(SessionModel.id == session_id)
            )
        ).one_or_none()
        if not proj_row:
            raise ValueError(f"Session {session_id} not linked to a project")
        project_id, exp_name = proj_row

        model_name = name or (exp_name or "model").strip()

        # Compute next version for (project_id, name).
        latest_version = (
            await db.execute(
                select(RegisteredModel.version)
                .where(
                    RegisteredModel.project_id == project_id,
                    RegisteredModel.name == model_name,
                )
                .order_by(desc(RegisteredModel.version))
                .limit(1)
            )
        ).scalar_one_or_none()
        next_version = (latest_version or 0) + 1

        # Copy artifact to a stable location.
        ext = artifact["path"].rsplit(".", 1)[-1]
        registry_path = (
            f"/projects/{project_id}/models/{model_name}/v{next_version}/model.{ext}"
        )
        try:
            data = await read_volume_file_async(artifact["path"])
            await write_to_volume(data, registry_path)
        except Exception as e:
            logger.exception("Failed to copy artifact: %s", e)
            raise

        metrics_summary = await _summarize_metrics(db, session_id)

        row = RegisteredModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            name=model_name,
            version=next_version,
            source_session_id=session_id,
            artifact_uri=registry_path,
            artifact_size_bytes=artifact["size"],
            metrics_summary=metrics_summary,
            framework=_detect_framework(artifact["path"]),
            status="ready",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.to_dict()


async def list_project_models(project_id: str) -> list[dict]:
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(RegisteredModel)
                    .where(RegisteredModel.project_id == project_id)
                    .order_by(desc(RegisteredModel.created_at))
                )
            )
            .scalars()
            .all()
        )
        return [r.to_dict() for r in rows]


async def get_model(model_id: str) -> dict | None:
    async with async_session() as db:
        row = (
            await db.execute(
                select(RegisteredModel).where(RegisteredModel.id == model_id)
            )
        ).scalar_one_or_none()
        return row.to_dict() if row else None


async def register_model_declared(
    *,
    experiment_id: str,
    path: str,
    framework: str,
    metrics: dict,
    description: str,
    training_dataset_id: int,
    validation_dataset_id: int | None = None,
    test_dataset_id: int | None = None,
    split_metrics: dict | None = None,
    hyperparams: dict | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Agent-declared registration: the agent calls register-model with
    an explicit artifact path + metrics. No volume walk, no extension
    sniffing. Marks the parent experiment as `trained` and emits SSE.

    Mandatory `training_dataset_id` (DatasetVersion.id) carries the data
    provenance into the registry — every model row directly answers the
    "what did this train on?" question instead of relying on the
    experiment_datasets join (which can include both raw + processed and
    confuse the lineage canvas). Optional `validation_dataset_id` /
    `test_dataset_id` add the eval split refs; `split_metrics` is an
    optional dict like {"train": {…}, "val": {…}, "test": {…}} so the
    user can see metrics next to each split in the UI.
    """
    from models import DatasetVersion, Experiment, ExperimentState

    if not description.strip():
        raise ValueError("description is required for register-model")
    if not path.strip():
        raise ValueError("path is required for register-model")
    if not isinstance(metrics, dict):
        raise ValueError("metrics must be a dict (e.g. {'accuracy': 0.91})")
    if not training_dataset_id:
        raise ValueError(
            "training_dataset_id is required — pass the dataset_version_id "
            "of the processed dataset you fit on. Without this, the "
            "lineage canvas can't tell what data the model saw."
        )

    async with async_session() as db:
        exp = (
            await db.execute(select(Experiment).where(Experiment.id == experiment_id))
        ).scalar_one_or_none()
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")
        project_id = exp.project_id
        sid = exp.session_id
        if not sid:
            # Fall back to the legacy direction: a session may point at
            # this experiment via Session.experiment_id even when the
            # new Experiment.session_id was never wired (the legacy
            # POST /api/experiments creates the pair this way). Pick
            # the earliest session if there are multiple.
            from models import Session as _S

            sid = (
                await db.execute(
                    select(_S.id)
                    .where(_S.experiment_id == experiment_id)
                    .order_by(_S.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        model_name = (name or exp.name or "model").strip() or "model"

        # Validate the dataset references exist + belong to this project.
        # Reject raw datasets for the training slot — the user wants the
        # graph to read Raw → Processed → Model, so processed data is the
        # canonical training input.
        ref_ids = [training_dataset_id]
        if validation_dataset_id:
            ref_ids.append(validation_dataset_id)
        if test_dataset_id:
            ref_ids.append(test_dataset_id)
        ref_rows = (
            (
                await db.execute(
                    select(DatasetVersion).where(DatasetVersion.id.in_(ref_ids))
                )
            )
            .scalars()
            .all()
        )
        ref_by_id = {r.id: r for r in ref_rows}
        for rid in ref_ids:
            if rid not in ref_by_id:
                raise ValueError(
                    f"dataset_version_id {rid} not found — call list-project-datasets"
                    " first to get the correct id."
                )
            if ref_by_id[rid].project_id != project_id:
                raise ValueError(
                    f"dataset_version_id {rid} belongs to a different project."
                )
        train_dv = ref_by_id[training_dataset_id]
        if train_dv.kind == "raw":
            raise ValueError(
                f"training_dataset_id {training_dataset_id} is a RAW dataset "
                f"({train_dv.name!r}). Run register-dataset first to declare "
                "the processed version, then pass that id here."
            )
        # Build the dataset_refs payload that's stored on the model row.
        # Each split_metrics entry becomes a ref. Splits whose dataset_id
        # was supplied get pinned to that DatasetVersion; the common
        # "one parquet with internal train/val/test split" case falls
        # back to `training_dataset_id` so val/test metrics still
        # surface in the UI without forcing the agent to fabricate
        # separate dataset rows.
        sm = split_metrics if isinstance(split_metrics, dict) else {}
        explicit_ids: dict[str, int | None] = {
            "train": training_dataset_id,
            "val": validation_dataset_id,
            "test": test_dataset_id,
        }
        dataset_refs: dict[str, dict] = {}
        # Always emit the "train" entry — it's the canonical edge into
        # the model and the call required training_dataset_id.
        dataset_refs["train"] = {
            "dataset_id": training_dataset_id,
            "metrics": sm.get("train") or {},
        }
        # Then any other split metrics, falling back to training_dataset_id.
        for role, m_metrics in sm.items():
            if role == "train":
                continue
            dv_id = explicit_ids.get(role) or training_dataset_id
            dataset_refs[role] = {
                "dataset_id": dv_id,
                "metrics": m_metrics or {},
            }
        # And any explicit dataset ids that weren't already covered.
        for role, dv_id in explicit_ids.items():
            if role == "train" or not dv_id or role in dataset_refs:
                continue
            dataset_refs[role] = {
                "dataset_id": dv_id,
                "metrics": sm.get(role) or {},
            }

        # Snapshot the session's Metric rows so the model's training
        # curves survive session deletion. Without this, the inline
        # charts on /models would silently disappear once a session is
        # cleaned up. We freeze the (step, name, value, stage, run_tag)
        # tuples; the source-of-truth Metric rows remain queryable for
        # the live metrics canvas as long as the session exists.
        from models import Metric as _Metric

        metrics_history: list[dict] = []
        if sid:
            rows = (
                (
                    await db.execute(
                        select(_Metric)
                        .where(_Metric.session_id == sid)
                        .order_by(_Metric.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            metrics_history = [
                {
                    "step": r.step,
                    "name": r.name,
                    "value": r.value,
                    "stage": r.stage,
                    "run_tag": r.run_tag,
                }
                for r in rows
            ]

        # Compute next version per (project_id, name).
        latest_version = (
            await db.execute(
                select(RegisteredModel.version)
                .where(
                    RegisteredModel.project_id == project_id,
                    RegisteredModel.name == model_name,
                )
                .order_by(desc(RegisteredModel.version))
                .limit(1)
            )
        ).scalar_one_or_none()
        next_version = (latest_version or 0) + 1

        ext = path.rsplit(".", 1)[-1] if "." in path else "bin"
        registry_path = (
            f"/projects/{project_id}/models/{model_name}/v{next_version}/model.{ext}"
        )

        # Best-effort copy: read the agent-declared path, write to the
        # stable registry path. We don't fail if the read fails (in tests
        # the volume is mocked) — the row still records the intent and
        # the original path so downstream code can resolve it.
        artifact_size = 0
        try:
            data = await read_volume_file_async(path)
            artifact_size = len(data)
            await write_to_volume(data, registry_path)
            artifact_uri = registry_path
        except Exception as e:
            logger.warning(
                "[register-model] failed to copy %s to registry path %s: %s",
                path,
                registry_path,
                e,
            )
            # Fall back to the original path so the row isn't useless.
            # Normalize: strip a leading `/data/` prefix if the agent
            # supplied a sandbox-mount path. The container also mounts
            # the volume at `/data`, so leaving the prefix in place
            # produces `/data/data/...` after the deploy codegen
            # prepends its own `/data/`. Volume paths are stored as
            # `/sessions/...` or `/projects/...`.
            artifact_uri = path
            if artifact_uri.startswith("/data/"):
                artifact_uri = artifact_uri[len("/data") :]

        row = RegisteredModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            experiment_id=experiment_id,
            name=model_name,
            version=next_version,
            source_session_id=sid,
            artifact_uri=artifact_uri,
            artifact_size_bytes=artifact_size,
            metrics_summary=metrics,
            description=description.strip(),
            hyperparams=hyperparams or {},
            dataset_refs=dataset_refs,
            metrics_history=metrics_history,
            framework=framework,
            status="ready",
        )
        db.add(row)

        # Transition experiment → trained as part of the same commit.
        exp.state = ExperimentState.TRAINED.value
        from datetime import datetime, timezone

        exp.completed_at = datetime.now(timezone.utc).isoformat()

        await db.commit()
        await db.refresh(row)
        result = row.to_dict()

    # SSE: tell the canvas to refetch lineage.
    if sid:
        try:
            from services.broadcaster import broadcaster

            await broadcaster.publish(
                sid,
                {"type": "model_registered", "data": result},
            )
        except Exception as e:
            logger.debug("SSE publish for model_registered skipped: %s", e)
    return result


async def find_session_model_artifact(session_id: str) -> dict | None:
    """Public wrapper — used by the router to gate the promote button."""
    art = await _find_model_artifact(session_id)
    if art is None:
        return None
    # Cross-check that something Artifact-typed `model` exists too, so
    # promotion intent matches the curated index — but don't *require* it
    # since the workspace is the source of truth.
    async with async_session() as db:
        had_model = (
            await db.execute(
                select(Artifact.id).where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "model",
                )
            )
        ).scalar_one_or_none()
    return {**art, "had_model_artifact_row": bool(had_model)}
