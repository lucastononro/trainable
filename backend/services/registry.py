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
