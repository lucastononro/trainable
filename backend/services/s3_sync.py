"""Sync processed data from Modal Volume to S3 after stage completion."""

import logging
import mimetypes

from services.s3_client import get_s3_client
from services.volume import listdir_async, read_volume_file_async, reload_volume_async

logger = logging.getLogger(__name__)


async def sync_stage_to_s3(session_id: str, experiment_id: str, stage: str) -> dict:
    """Sync all files from a session workspace on Modal Volume to S3.

    Uploads the entire /sessions/{session_id}/ workspace to
    s3://datasets/datasets/{experiment_id}/processed/{session_id}/

    `stage` is accepted for compatibility and recorded in the result, but
    no longer partitions the path — sessions are now flat, free-form
    workspaces shared across agents.
    """

    await reload_volume_async()
    s3 = get_s3_client()
    bucket = "datasets"
    s3_prefix = f"datasets/{experiment_id}/processed/{session_id}"

    workspace = f"/sessions/{session_id}"
    synced_files = []

    try:
        entries = await listdir_async(workspace, recursive=True)
    except Exception as e:
        logger.error(f"[S3_SYNC] Failed to list {workspace}: {e}")
        return {
            "session_id": session_id,
            "files_synced": 0,
            "files": [],
            "error": str(e),
        }

    for entry in entries:
        if entry.type.name != "FILE":
            continue

        rel_path = entry.path
        if rel_path.startswith(workspace + "/"):
            rel_path = rel_path[len(workspace) + 1 :]
        elif rel_path.startswith(workspace):
            rel_path = rel_path[len(workspace) :]
        rel_path = rel_path.lstrip("/")

        s3_key = f"{s3_prefix}/{rel_path}"
        content_type = mimetypes.guess_type(rel_path)[0] or "application/octet-stream"

        try:
            data = await read_volume_file_async(entry.path)
            s3.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=data,
                ContentType=content_type,
            )
            synced_files.append(
                {
                    "volume_path": entry.path,
                    "s3_key": s3_key,
                    "s3_uri": f"s3://{bucket}/{s3_key}",
                    "size": len(data),
                }
            )
            logger.info(
                f"[S3_SYNC] {entry.path} -> s3://{bucket}/{s3_key} ({len(data)} bytes)"
            )
        except Exception as e:
            logger.error(f"[S3_SYNC] Failed to sync {entry.path}: {e}")

    return {
        "session_id": session_id,
        "experiment_id": experiment_id,
        "stage": stage,
        "s3_prefix": s3_prefix,
        "files_synced": len(synced_files),
        "files": synced_files,
    }
