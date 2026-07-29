"""SSE publishing, DB persistence, and post-stage hooks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from db import async_session
from models import Artifact, Message, Task
from services.broadcaster import broadcaster
from services.metadata_extractor import extract_and_store_metadata
from services.s3_sync import sync_stage_to_s3
from services.validator import validate_prep_output, validate_train_output
from services.volume import listdir_async, read_volume_file_async

logger = logging.getLogger(__name__)


async def save_and_publish(
    session_id: str,
    event_type: str,
    data: dict,
    role: str | None = None,
    publish: bool = True,
    agent_meta: dict | None = None,
):
    """Persist a chat event to the DB and optionally publish via SSE.

    agent_meta carries per-agent identity (agent_id, agent_type, parent_agent_id, depth)
    and is merged into the row's metadata so downstream tools can filter by agent.
    """

    # Publish to SSE immediately. Mirror agent_meta into the SSE payload so the
    # frontend (and any other listener) can attribute events without re-querying.
    if publish:
        sse_data = dict(data)
        if agent_meta:
            sse_data.setdefault("agent_id", agent_meta.get("agent_id"))
            sse_data.setdefault("agent_type", agent_meta.get("agent_type"))
            sse_data.setdefault("parent_agent_id", agent_meta.get("parent_agent_id"))
            sse_data.setdefault("depth", agent_meta.get("depth"))
        await broadcaster.publish(session_id, {"type": event_type, "data": sse_data})

    # Persist to DB
    if role:
        try:
            async with async_session() as db:
                metadata = {
                    "event_type": event_type,
                    **{k: v for k, v in data.items() if k not in ("text", "content")},
                }
                if agent_meta:
                    for k, v in agent_meta.items():
                        if v is not None:
                            metadata.setdefault(k, v)
                db.add(
                    Message(
                        session_id=session_id,
                        role=role,
                        content=data.get("text", data.get("content", "")),
                        metadata_=metadata,
                    )
                )
                await db.commit()
        except Exception as e:
            logger.error("Failed to save message: %s", e)


async def publish_artifacts(session_id: str, experiment_id: str, stage: str):
    """After an agent run completes, scan the session workspace for new files,
    publish via SSE, and persist as Artifact records.

    The `stage` argument here is the producer agent_type (e.g. "eda", "trainer").
    It's recorded on each Artifact row so the UI can attribute outputs to a
    specific agent, but it no longer implies a folder name — the agent is
    free to organize its outputs anywhere under /sessions/{session_id}/.
    """

    workspace = f"/sessions/{session_id}"

    # Report discovery: prefer a top-level report.md (the soft convention);
    # otherwise the most recently modified .md file anywhere in the workspace.
    report_path: str | None = None
    report_mtime = -1.0
    try:
        for entry in await listdir_async(workspace, recursive=True):
            if entry.type.name != "FILE" or not entry.path.endswith(".md"):
                continue
            rel = (
                entry.path[len(workspace) + 1 :]
                if entry.path.startswith(workspace + "/")
                else entry.path
            )
            if rel == "report.md":
                report_path = entry.path
                break
            mtime = float(getattr(entry, "mtime", 0) or 0)
            if mtime > report_mtime:
                report_mtime = mtime
                report_path = entry.path
    except Exception as e:
        logger.debug("Workspace listdir failed while looking for report: %s", e)

    if report_path:
        try:
            report_data = await read_volume_file_async(report_path)
            report_text = report_data.decode("utf-8", errors="replace")
            if report_text.strip():
                await save_and_publish(
                    session_id,
                    "report_ready",
                    {"content": report_text, "stage": stage, "path": report_path},
                    role="assistant",
                )
                logger.info(
                    "Published report from %s (%d chars)", report_path, len(report_text)
                )
        except Exception as e:
            logger.debug("Could not read report %s: %s", report_path, e)

    # List all generated files and persist as Artifact records
    try:
        files = []
        for entry in await listdir_async(workspace, recursive=True):
            if entry.type.name != "FILE":
                continue
            files.append(
                {
                    "path": entry.path,
                    "type": "file",
                }
            )
        if files:
            await save_and_publish(
                session_id,
                "files_ready",
                {"files": files, "stage": stage, "workspace": workspace},
                role="system",
            )
            logger.info("Published %d files", len(files))

            # Persist artifacts to DB. Agents now share one workspace per
            # session, so dedupe on (session_id, path) — earlier agents'
            # files shouldn't be re-attributed to the current agent.
            try:
                async with async_session() as db:
                    existing = await db.execute(
                        select(Artifact.path).where(Artifact.session_id == session_id)
                    )
                    existing_paths = {p for (p,) in existing.all()}
                    new_count = 0
                    for f in files:
                        if f["path"] in existing_paths:
                            continue
                        name = f["path"].split("/")[-1]
                        if name.endswith(".json"):
                            artifact_type = "metadata"
                        elif name.endswith(".md"):
                            artifact_type = "report"
                        elif name.endswith(".html"):
                            artifact_type = "html"
                        elif name.endswith((".png", ".jpg", ".jpeg", ".svg")):
                            artifact_type = "chart"
                        elif name.endswith((".parquet", ".csv")):
                            artifact_type = "dataset"
                        elif name.endswith((".pkl", ".joblib", ".pt", ".h5")):
                            artifact_type = "model"
                        elif name.endswith(".py"):
                            artifact_type = "script"
                        else:
                            artifact_type = "file"
                        db.add(
                            Artifact(
                                session_id=session_id,
                                stage=stage,
                                artifact_type=artifact_type,
                                name=name,
                                path=f["path"],
                            )
                        )
                        new_count += 1
                    await db.commit()
                logger.info("Persisted %d new artifacts to DB", new_count)
            except Exception as e:
                logger.error("Failed to persist artifacts: %s", e)
    except Exception as e:
        logger.warning("Could not list workspace files: %s", e)


async def post_stage_hook(session_id: str, experiment_id: str, stage: str):
    """Run validation, S3 sync, and metadata extraction after a stage completes.

    Failures here are non-blocking -- the stage is still marked as done.
    """

    # 1. Validation. `stage` here is the agent_type that just finished —
    # accept both the legacy short names ("prep"/"train") and the current
    # multi-agent names ("data_prep"/"trainer").
    try:
        if stage in ("prep", "data_prep"):
            validation = await validate_prep_output(session_id, experiment_id)
        elif stage in ("train", "trainer"):
            validation = await validate_train_output(session_id, experiment_id)
        else:
            validation = None

        if validation:
            n_errors = len(validation.get("errors", []))
            n_warnings = len(validation.get("warnings", []))
            n_passed = len(validation.get("passed", []))
            await save_and_publish(
                session_id, "validation_result", validation, role="system"
            )
            logger.info(
                "Validation: %d passed, %d warnings, %d errors",
                n_passed,
                n_warnings,
                n_errors,
            )
    except Exception as e:
        logger.error("Post-hook validation failed: %s", e)

    # 2. S3 sync (after prep and train agents finish)
    if stage in ("prep", "data_prep", "train", "trainer"):
        try:
            sync_result = await sync_stage_to_s3(session_id, experiment_id, stage)
            await save_and_publish(
                session_id,
                "s3_sync_complete",
                {
                    "stage": stage,
                    "files_synced": sync_result["files_synced"],
                    "s3_prefix": sync_result.get("s3_prefix", ""),
                },
            )
            logger.info("S3 sync: %d files synced", sync_result["files_synced"])

            # Update Artifact records with S3 paths
            if sync_result["files"]:
                try:
                    async with async_session() as db:
                        for f in sync_result["files"]:
                            result = await db.execute(
                                select(Artifact).where(
                                    Artifact.session_id == session_id,
                                    Artifact.path == f["volume_path"],
                                )
                            )
                            artifact = result.scalar_one_or_none()
                            if artifact:
                                artifact.s3_path = f["s3_uri"]
                        await db.commit()
                except Exception as e:
                    logger.error("Failed to update artifact S3 paths: %s", e)
        except Exception as e:
            logger.error("Post-hook S3 sync failed: %s", e)

    # 3. Metadata extraction (after data prep only)
    if stage in ("prep", "data_prep"):
        try:
            await extract_and_store_metadata(session_id, experiment_id)
            await save_and_publish(
                session_id,
                "metadata_ready",
                {
                    "session_id": session_id,
                },
            )
            logger.info("Post-hook metadata extraction complete")
        except Exception as e:
            logger.error("Post-hook metadata extraction failed: %s", e)

    # 3.5 Abandoned-experiment cleanup. If the trainer (or any agent)
    # called start-training but never reached register-model, the
    # experiment row is stuck in TRAINING. Sweep those into ABANDONED so
    # the lineage view can render a warning chip instead of a permanent
    # spinner. Runs for every stage to be safe — the no-op cost is low.
    try:
        from services.experiments import transition_abandoned_in_session

        n = await transition_abandoned_in_session(session_id)
        if n:
            logger.info("[post-stage] Auto-abandoned %d in-flight experiment(s)", n)
    except Exception as e:
        logger.error("Post-hook abandoned-experiment cleanup failed: %s", e)

    # 3.6 Stale-task sweep. Agents are instructed to close every
    # `in_progress` task before their turn ends; if they forget, the
    # tasks card shows a permanent spinner that makes the user think
    # work is still happening. Flip any leftover `in_progress` rows
    # back to `pending` and emit `task_updated` so the UI updates.
    try:
        async with async_session() as db:
            stmt = select(Task).where(
                Task.session_id == session_id,
                Task.status == "in_progress",
            )
            stale = (await db.execute(stmt)).scalars().all()
            now_iso = datetime.now(timezone.utc).isoformat()
            for t in stale:
                t.status = "pending"
                t.updated_at = now_iso
            if stale:
                await db.commit()
                payloads = [t.to_dict() for t in stale]
            else:
                payloads = []
        for payload in payloads:
            await save_and_publish(session_id, "task_updated", payload, role="system")
        if payloads:
            logger.info(
                "[post-stage] Reset %d stale in_progress task(s)", len(payloads)
            )
    except Exception as e:
        logger.error("Post-hook stale-task sweep failed: %s", e)

    # 4. Reproducibility snapshot (after training only)
    if stage in ("train", "trainer"):
        try:
            from services.snapshot import take_snapshot

            snap = await take_snapshot(session_id)
            await save_and_publish(
                session_id,
                "snapshot_ready",
                {
                    "session_id": session_id,
                    "dataset_hash": snap.get("dataset_hash"),
                    "code_hash": snap.get("code_hash"),
                    "manifest_uri": snap.get("manifest_uri"),
                },
            )
            logger.info("Post-hook snapshot captured: %s", snap.get("manifest_uri"))
        except Exception as e:
            logger.error("Post-hook snapshot failed: %s", e)
