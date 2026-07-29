"""Shared dataset path helpers — S3 keys, Modal Volume paths, dataset_ref.

Single home for the path layout every upload route agrees on (previously
private helpers in `routers/experiments.py`, imported across router modules
by `routers/samples.py`).
"""

from fastapi import HTTPException


def safe_relative_path(raw: str) -> str:
    """Sanitize a user-supplied relative path so it can be safely used as part
    of an S3 key / volume path.

    - Strips leading / and whitespace.
    - Normalises backslashes to forward slashes.
    - Rejects any segment that equals '..' (path-traversal guard).
    - Collapses empty segments (// becomes /).
    - Falls back to "file" if the input is empty after cleanup.
    """
    if not raw:
        return "file"
    raw = raw.replace("\\", "/").strip()
    # Drop any leading slashes (we never want an absolute path on S3 side).
    while raw.startswith("/"):
        raw = raw[1:]
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        # Don't allow escaping the project root.
        raise HTTPException(status_code=400, detail=f"Invalid path segment in: {raw!r}")
    cleaned = "/".join(parts)
    return cleaned or "file"


def dataset_s3_key(project_id: str, relative_path: str) -> str:
    """Data is owned by the project. Every chat in the project sees the same
    files at the same path, so we don't scope by experiment_id anymore."""
    return f"datasets/projects/{project_id}/{safe_relative_path(relative_path)}"


def dataset_volume_path(project_id: str, relative_path: str) -> str:
    return f"/projects/{project_id}/datasets/{safe_relative_path(relative_path)}"


def dataset_ref_for(project_id: str, uploaded: list[str]) -> str:
    """Return single-file path when there's one upload, else the project prefix."""
    if len(uploaded) == 1:
        return uploaded[0]
    return f"s3://datasets/projects/{project_id}/"
