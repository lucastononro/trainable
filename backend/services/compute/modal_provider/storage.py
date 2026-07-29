"""Modal Volume implementation of StorageBackend.

The Volume handle stays owned by services.volume (`get_volume`) so the
many tests and callers that patch `services.volume.get_volume` keep
working; this class holds the Modal-specific mechanics that used to live
inline in each services.volume helper.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from services.compute.base import StorageBackend

logger = logging.getLogger(__name__)


def _vol():
    import services.volume as volume_mod  # late: avoids import cycle

    return volume_mod.get_volume()


class ModalStorage(StorageBackend):
    name = "modal"

    async def read_file(self, path: str) -> bytes:
        def _sync() -> bytes:
            return b"".join(_vol().read_file(path))

        return await asyncio.get_running_loop().run_in_executor(None, _sync)

    async def listdir(self, path: str, recursive: bool = False) -> list:
        # Modal's FileEntry already duck-types compute.base.FileEntry
        # (`.path`, `.type.name`), so entries pass through untouched.
        def _sync() -> list:
            return list(_vol().listdir(path, recursive=recursive))

        return await asyncio.get_running_loop().run_in_executor(None, _sync)

    async def upload(self, local_path: str, remote_path: str) -> None:
        vol = _vol()

        def _sync():
            with vol.batch_upload(force=True) as batch:
                batch.put_file(local_path, remote_path)

        await asyncio.get_running_loop().run_in_executor(None, _sync)

    async def upload_many(self, pairs: list[tuple[str, str]]) -> int:
        if not pairs:
            return 0
        vol = _vol()

        # ONE batch_upload() context for the whole list — Modal ships the
        # payload in a single round-trip rather than one per file.
        def _sync():
            with vol.batch_upload(force=True) as batch:
                for local_path, remote_path in pairs:
                    batch.put_file(local_path, remote_path)

        await asyncio.get_running_loop().run_in_executor(None, _sync)
        return len(pairs)

    async def remove(self, path: str) -> None:
        vol = _vol()

        def _sync():
            vol.remove_file(path, recursive=True)

        await asyncio.get_running_loop().run_in_executor(None, _sync)

    async def write(self, content: str | bytes, remote_path: str) -> None:
        vol = _vol()
        is_bytes = isinstance(content, (bytes, bytearray, memoryview))

        def _sync():
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

        await asyncio.get_running_loop().run_in_executor(None, _sync)

    async def ensure_session_workspace(self, session_id: str) -> None:
        vol = _vol()

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

    async def reload(self) -> bool:
        def _sync() -> bool:
            return self.reload_sync()

        return await asyncio.get_running_loop().run_in_executor(None, _sync)

    def reload_sync(self) -> bool:
        # Modal's `Volume.reload()` raises "reload() can only be called
        # from within a running function" on some SDK versions when called
        # from a plain Python process. Swallow it — the subsequent
        # listdir() still works with last-known state.
        try:
            _vol().reload()
            return True
        except Exception as e:
            logger.debug("Volume.reload() skipped: %s", e)
            return False
