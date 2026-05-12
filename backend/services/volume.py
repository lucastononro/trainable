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
    """Read a complete file from the Modal Volume (sync — thread-only)."""
    return b"".join(get_volume().read_file(path))


async def read_volume_file_async(path: str) -> bytes:
    """Read a file from the Modal Volume without blocking the event loop.

    Wraps the sync `vol.read_file(...)` generator on the default thread pool.
    Modal's `read_file.aio(...)` shape varies across SDK versions, so we
    defer to the well-tested sync path and just keep it off the loop.
    """

    def _sync() -> bytes:
        return b"".join(get_volume().read_file(path))

    return await asyncio.get_running_loop().run_in_executor(None, _sync)


async def listdir_async(path: str, recursive: bool = False) -> list:
    """List a directory on the Modal Volume without blocking the event loop.

    Modal's `Volume.listdir` returns a plain generator that is awkward to
    iterate off-loop natively (`.aio` isn't uniformly available across SDK
    versions). Wrapping it on the default executor keeps the event loop
    free while relying on the well-tested sync call.
    """

    def _sync() -> list:
        return list(get_volume().listdir(path, recursive=recursive))

    return await asyncio.get_running_loop().run_in_executor(None, _sync)


async def reload_volume_async() -> bool:
    """Async version of `reload_volume` — thread-pool wrapped for safety."""

    def _sync() -> bool:
        try:
            get_volume().reload()
            return True
        except Exception as e:
            logger.debug("Volume.reload() skipped: %s", e)
            return False

    return await asyncio.get_running_loop().run_in_executor(None, _sync)


async def upload_to_volume(local_path: str, remote_path: str):
    """Upload a local file to the Modal Volume (non-blocking)."""
    vol = get_volume()

    def _sync_upload():
        with vol.batch_upload(force=True) as batch:
            batch.put_file(local_path, remote_path)

    await asyncio.get_running_loop().run_in_executor(None, _sync_upload)
    logger.info("Uploaded %s -> %s", local_path, remote_path)


async def upload_many_to_volume(pairs: list[tuple[str, str]]) -> int:
    """Bulk-upload many files to the Modal Volume in a single batch.

    `pairs` is a list of (local_path, remote_path). Critically, this opens
    ONE batch_upload() context for the whole list — Modal then ships the
    payload in a single round-trip rather than one per file. The 1-by-1
    `upload_to_volume()` is a 30-min-for-1k-files trap; this is the bulk
    path that should be used for any folder upload.

    Returns the number of files actually pushed.
    """
    if not pairs:
        return 0
    vol = get_volume()

    def _sync_upload():
        with vol.batch_upload(force=True) as batch:
            for local_path, remote_path in pairs:
                batch.put_file(local_path, remote_path)

    await asyncio.get_running_loop().run_in_executor(None, _sync_upload)
    logger.info("Bulk-uploaded %d files to Modal Volume", len(pairs))
    return len(pairs)


async def remove_volume_file_async(path: str):
    """Remove a file from the Modal Volume without blocking the event loop."""
    vol = get_volume()

    def _sync():
        vol.remove_file(path, recursive=True)

    await asyncio.get_running_loop().run_in_executor(None, _sync)
    logger.info("Removed %s", path)


async def ensure_session_workspace(session_id: str) -> None:
    """Ensure `/sessions/{sid}/src/__init__.py` exists on the Modal Volume.

    Setting `workdir=/data/sessions/{sid}` on a Sandbox requires the directory
    to exist when Python starts. For a brand-new session, no agent has written
    there yet, so we lay down an empty `src/__init__.py` first. Idempotent —
    safe to call before every sandbox spawn.
    """
    import tempfile

    vol = get_volume()

    def _sync():
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
                tmp = f.name
            with vol.batch_upload(force=False) as batch:
                batch.put_file(tmp, f"/sessions/{session_id}/src/__init__.py")
            os.unlink(tmp)
        except Exception as e:
            logger.debug("ensure_session_workspace skipped: %s", e)

    await asyncio.get_running_loop().run_in_executor(None, _sync)


async def write_to_volume(content: str | bytes, remote_path: str):
    """Write file content directly to the Modal Volume (non-blocking).

    Accepts both `str` (text) and `bytes`. Every model-promotion caller hands
    in bytes from `read_volume_file_async`; the original `mode="w"` raised
    `TypeError` on every such call, which was swallowed by
    `register_model_declared`'s best-effort copy block — so the advertised
    `/projects/{pid}/models/.../v{N}/model.{ext}` registry artifact never
    actually landed.
    """
    vol = get_volume()
    is_bytes = isinstance(content, (bytes, bytearray, memoryview))

    def _sync_write():
        if is_bytes:
            f = tempfile.NamedTemporaryFile(mode="wb", suffix=".bin", delete=False)
            payload = bytes(content)
        else:
            f = tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            )
            payload = content
        try:
            with f:
                f.write(payload)
                tmp = f.name
            with vol.batch_upload(force=True) as batch:
                batch.put_file(tmp, remote_path)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    await asyncio.get_running_loop().run_in_executor(None, _sync_write)
    logger.info("Wrote %dB -> %s", len(content), remote_path)
