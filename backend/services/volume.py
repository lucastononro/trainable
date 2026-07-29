"""Workspace storage helpers — centralized access to the shared data volume.

These functions are a stable facade over the provider-selected
StorageBackend (services.compute.get_storage): Modal Volume by default,
RunPod network volume (S3 API) when COMPUTE_PROVIDER=runpod. Call sites
keep importing from services.volume; only the mechanics moved into
services/compute/*_provider/storage.py.

`get_volume()` (the raw Modal Volume handle) stays here for the Modal
adapter, the kernel spawn path and tests that patch it by name.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import modal

from config import settings

logger = logging.getLogger(__name__)

_volume = None

# Path segments hidden from user-facing file listings (tree route, S3 browser,
# list-session-files skill). The agent still writes them via execute-code; we
# just don't surface them. Match by whole segment, not glob suffix.
WORKSPACE_IGNORE_SEGMENTS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".ipynb_checkpoints",
        ".DS_Store",
        ".git",
    }
)
WORKSPACE_IGNORE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".pyd", ".egg-info")


def should_ignore_workspace_path(path: str) -> bool:
    """Return True if `path` is build noise the user should not see.

    Splits on `/` and checks every segment. A path is ignored if any segment
    matches `WORKSPACE_IGNORE_SEGMENTS` or its basename ends with any
    `WORKSPACE_IGNORE_SUFFIXES`.
    """
    if not path:
        return False
    parts = [p for p in path.split("/") if p]
    if any(p in WORKSPACE_IGNORE_SEGMENTS for p in parts):
        return True
    basename = parts[-1] if parts else ""
    return basename.endswith(WORKSPACE_IGNORE_SUFFIXES)


def get_volume():
    """Return a lazily-initialized Modal Volume (Modal provider only)."""
    global _volume
    if _volume is None:
        _volume = modal.Volume.from_name(
            settings.modal_volume_name, create_if_missing=True
        )
    return _volume


def _storage():
    from services.compute import get_storage

    return get_storage()


def reload_volume() -> bool:
    """Ensure the storage view reflects the latest sandbox writes (sync).

    Returns True if reload succeeded (or the backend is always-live),
    False if it was skipped.
    """
    return _storage().reload_sync()


def read_volume_file(path: str) -> bytes:
    """Read a complete file from the volume (sync — thread-only).

    Modal-only sync path kept for legacy callers running off-loop.
    """
    return b"".join(get_volume().read_file(path))


async def read_volume_file_async(path: str) -> bytes:
    """Read a file from the volume without blocking the event loop."""
    return await _storage().read_file(path)


async def iter_volume_file_chunks_async(
    path: str, *, chunk_size: int = 1024 * 1024
) -> AsyncIterator[bytes]:
    """Yield volume file bytes without joining the whole file in memory.

    Streaming is Modal-specific (the Volume handle's chunked read_file);
    other storage backends fall back to a whole-object read through the
    storage facade — their APIs have no streaming get.
    """

    if _storage().name != "modal":
        data = await _storage().read_file(path)
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]
        return

    sentinel = object()

    def _open():
        return iter(get_volume().read_file(path))

    def _next(iterator):
        try:
            return next(iterator)
        except StopIteration:
            return sentinel

    loop = asyncio.get_running_loop()
    iterator = await loop.run_in_executor(None, _open)
    while True:
        chunk = await loop.run_in_executor(None, _next, iterator)
        if chunk is sentinel:
            break
        data = bytes(chunk)
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]


async def listdir_async(path: str, recursive: bool = False) -> list:
    """List a directory on the volume without blocking the event loop.

    Entries expose `.path` and `.type.name` ("FILE" / "DIRECTORY") —
    Modal's native FileEntry for the modal backend, the normalized
    services.compute.FileEntry for others.
    """
    return await _storage().listdir(path, recursive=recursive)


async def reload_volume_async() -> bool:
    """Async version of `reload_volume`."""
    return await _storage().reload()


async def upload_to_volume(local_path: str, remote_path: str):
    """Upload a local file to the volume (non-blocking)."""
    await _storage().upload(local_path, remote_path)
    logger.info("Uploaded %s -> %s", local_path, remote_path)


async def upload_many_to_volume(pairs: list[tuple[str, str]]) -> int:
    """Bulk-upload many files to the volume in a single batch.

    `pairs` is a list of (local_path, remote_path). This is the bulk path
    that should be used for any folder upload — uploading 1-by-1 via
    `upload_to_volume()` is a 30-min-for-1k-files trap.

    Returns the number of files actually pushed.
    """
    count = await _storage().upload_many(pairs)
    if count:
        logger.info("Bulk-uploaded %d files to the volume", count)
    return count


async def remove_volume_file_async(path: str):
    """Remove a file from the volume without blocking the event loop."""
    await _storage().remove(path)
    logger.info("Removed %s", path)


async def ensure_session_workspace(session_id: str) -> None:
    """Ensure `/sessions/{sid}/src/__init__.py` exists on the volume.

    Setting `workdir=/data/sessions/{sid}` on a sandbox requires the
    directory to exist when Python starts. For a brand-new session, no
    agent has written there yet, so we lay down an empty `src/__init__.py`
    first. Idempotent — safe to call before every sandbox spawn.
    """
    await _storage().ensure_session_workspace(session_id)


async def write_to_volume(content: str | bytes, remote_path: str):
    """Write file content directly to the volume (non-blocking).

    Accepts both `str` (text) and `bytes` — model-promotion callers hand
    in bytes from `read_volume_file_async`.
    """
    await _storage().write(content, remote_path)
    logger.info("Wrote %dB -> %s", len(content), remote_path)
