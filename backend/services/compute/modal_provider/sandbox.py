"""Modal implementation of SandboxProvider.

The Modal primitives (App, Image, Volume handles) intentionally stay
defined on the legacy modules (services.sandbox / services.volume) and are
resolved through those module namespaces at call time: existing tests and
callers monkeypatch `services.sandbox.modal.Sandbox.create`, `_get_app`,
`_get_image` and `services.volume.get_volume`, and those patch points must
keep working unchanged.
"""

from __future__ import annotations

from services.compute.base import SandboxProvider


class ModalSandboxHandle:
    """Thin adapter over modal.Sandbox — normalizes `.wait.aio()` to
    `wait()` and `.terminate.aio()` to `terminate()`."""

    def __init__(self, sb):
        self._sb = sb
        self.stdout = sb.stdout
        self.stderr = sb.stderr

    @property
    def returncode(self) -> int | None:
        return self._sb.returncode

    async def wait(self) -> int | None:
        await self._sb.wait.aio()
        return self._sb.returncode

    async def terminate(self) -> None:
        await self._sb.terminate.aio()


class ModalSandboxProvider(SandboxProvider):
    name = "modal"

    async def create(
        self,
        *,
        code: str,
        session_id: str,
        gpu: str | None,
        timeout: int,
        workdir: str,
    ) -> ModalSandboxHandle:
        import services.sandbox as sandbox_mod  # late: avoids import cycle

        sb = await sandbox_mod.modal.Sandbox.create.aio(
            "python",
            "-u",
            "-c",
            code,
            image=sandbox_mod._get_image(),
            volumes={"/data": sandbox_mod.get_volume()},
            gpu=gpu,
            timeout=timeout,
            workdir=workdir,
            app=await sandbox_mod._get_app(),
        )
        return ModalSandboxHandle(sb)
