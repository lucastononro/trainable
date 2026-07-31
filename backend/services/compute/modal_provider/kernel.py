"""Modal implementation of KernelTransport.

Wraps the long-lived modal.Sandbox that runs the kernel proxy script and
speaks the newline-JSON protocol over its stdin/stdout. Modal primitives
are resolved through services.kernel_manager's module namespace at call
time — tests patch `km.modal.Sandbox`, `km.get_app`, `km.get_image` and
`km.get_volume`, and those patch points must keep working unchanged.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class ModalKernelTransport:
    def __init__(self, sb):
        self._sb = sb
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        try:
            async for chunk in self._sb.stderr:
                logger.debug("kernel stderr: %s", chunk[:500])
        except Exception:
            pass

    async def send(self, line: str) -> None:
        # Modal's _StreamWriter: `write` is a synchronous buffer call,
        # only `drain` is awaited. Neither is a blueprint method.
        self._sb.stdin.write((line + "\n").encode("utf-8"))
        await self._sb.stdin.drain.aio()

    async def events(self):
        buf = ""
        async for chunk in self._sb.stdout:
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line:
                    yield line

    async def terminate(self) -> None:
        try:
            await self._sb.terminate.aio()
        finally:
            if not self._stderr_task.done():
                self._stderr_task.cancel()


async def create_modal_kernel_transport(session_id: str) -> ModalKernelTransport:
    import services.kernel_manager as km  # late: avoids import cycle

    sb = await km.modal.Sandbox.create.aio(
        "python",
        "-u",
        "-c",
        km.build_kernel_proxy_script(session_id),
        image=km.get_image(),
        volumes={"/data": km.get_volume()},
        timeout=km.KERNEL_MAX_LIFETIME_S,
        # Anchor cwd to the session workspace so notebook cells that use
        # relative paths (`open("data/x.parquet")`) land on the volume.
        workdir=f"/data/sessions/{session_id}",
        app=await km.get_app(),
    )
    return ModalKernelTransport(sb)
