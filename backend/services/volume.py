"""Modal Volume helpers — centralized access to the shared data volume."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

import modal

from config import settings

logger = logging.getLogger(__name__)

_volume = None


def get_volume():
    """Return a lazily-initialized Modal Volume."""
    global _volume
    if _volume is None:
        _volume = modal.Volume.from_name(
            settings.modal_volume_name, create_if_missing=True
        )
    return _volume


def reload_volume() -> bool:
    """Ensure the volume cache reflects the latest sandbox writes.

    Modal's `Volume.reload()` raises "reload() can only be called from within
    a running function" on some SDK versions when called from a plain Python
    process (i.e. the FastAPI backend). We swallow that error because the
    subsequent `listdir()` still works with the last-known state, which is
    good enough for the UI. Returns True if reload succeeded, False if it
    was skipped.
    """
    try:
        get_volume().reload()
        return True
    except Exception as e:
        logger.debug("Volume.reload() skipped: %s", e)
        return False


def read_volume_file(path: str) -> bytes:
    """Read a complete file from the Modal Volume."""
    return b"".join(get_volume().read_file(path))


async def upload_to_volume(local_path: str, remote_path: str):
    """Upload a local file to the Modal Volume (non-blocking)."""
    vol = get_volume()

    def _sync_upload():
        with vol.batch_upload(force=True) as batch:
            batch.put_file(local_path, remote_path)

    await asyncio.get_running_loop().run_in_executor(None, _sync_upload)
    logger.info("Uploaded %s -> %s", local_path, remote_path)


async def write_to_volume(content: str, remote_path: str):
    """Write text content directly to the Modal Volume (non-blocking)."""
    vol = get_volume()

    def _sync_write():
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(content)
            tmp = f.name
        try:
            with vol.batch_upload(force=True) as batch:
                batch.put_file(tmp, remote_path)
        finally:
            os.unlink(tmp)

    await asyncio.get_running_loop().run_in_executor(None, _sync_write)
    logger.info("Wrote %dB -> %s", len(content), remote_path)
