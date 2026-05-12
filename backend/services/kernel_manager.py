"""Persistent Jupyter kernels running in Modal Sandboxes, one per session.

Architecture: each session owns a long-lived Modal Sandbox that runs
`python -u -c <KERNEL_PROXY_SCRIPT>`. The proxy starts a real `ipykernel`
subprocess inside the sandbox and speaks ZMQ to it *locally* via
`jupyter_client`. The Trainable backend drives the proxy over the sandbox's
stdin/stdout using newline-delimited JSON — no Modal tunnels required.

Kernel events flow:
    backend --stdin JSON--> proxy --ZMQ--> ipykernel
    ipykernel --ZMQ--> proxy --stdout JSON--> backend --SSE--> frontend
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import modal

from services import notebook_store
from services.broadcaster import broadcaster
from services.sandbox import SDK_PREAMBLE, build_sdk_preamble, get_app, get_image
from services.volume import ensure_session_workspace, get_volume

logger = logging.getLogger(__name__)


KERNEL_IDLE_TIMEOUT_S = 15 * 60
KERNEL_MAX_LIFETIME_S = 2 * 60 * 60
KERNEL_READY_TIMEOUT_S = 120
REAPER_POLL_S = 60

# Size caps for a single output, to protect SSE + the .ipynb on disk.
MAX_STREAM_CHARS = 100_000
MAX_TEXT_PLAIN_CHARS = 100_000
MAX_HTML_CHARS = 1_000_000
MAX_PNG_BYTES = 5_000_000  # ~6.7 MB base64


def _cap_text(text: str, limit: int, label: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{label} truncated: {len(text) - limit} chars omitted]"


def _cap_display_data(data: dict) -> dict:
    """Trim oversized MIME bundles in-place-safe (returns a new dict)."""
    if not data:
        return data
    out = {}
    for mime, value in data.items():
        if mime == "image/png":
            # base64-encoded string
            text = value if isinstance(value, str) else "".join(value)
            if len(text) > MAX_PNG_BYTES:
                out[mime] = f"[truncated png: {len(text)} > {MAX_PNG_BYTES} chars]"
                # Also drop alt reps that could be just as large.
                continue
            out[mime] = text
        elif mime == "text/html":
            text = value if isinstance(value, str) else "".join(value)
            out[mime] = _cap_text(text, MAX_HTML_CHARS, "html")
        elif mime == "text/plain":
            text = value if isinstance(value, str) else "".join(value)
            out[mime] = _cap_text(text, MAX_TEXT_PLAIN_CHARS, "text/plain")
        else:
            out[mime] = value
    return out


# In-sandbox kernel proxy. Kept as a string so it ships via `python -u -c <...>`
# and we don't need to pre-upload anything to the Volume. The proxy owns the
# jupyter_client lifecycle; Trainable never talks ZMQ across the network.
#
# `__SDK_PREAMBLE_LITERAL__` is replaced at module import time (below) with a
# repr()-safe Python string literal of the trainable SDK preamble. The proxy
# sends it as a silent execute before any user cells, so `sys.modules['trainable']`
# is registered in the ipykernel process just like it is in one-shot execute_code
# scripts.
_KERNEL_PROXY_SCRIPT_TEMPLATE = r"""
import asyncio, json, sys, traceback
from jupyter_client.manager import AsyncKernelManager

_SDK_PREAMBLE = __SDK_PREAMBLE_LITERAL__

def _emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

async def main():
    km = AsyncKernelManager(kernel_name="python3")
    await km.start_kernel()
    kc = km.client()
    kc.start_channels()
    try:
        await kc.wait_for_ready(timeout=60)
    except Exception as e:
        _emit({"type": "fatal", "error": f"kernel-not-ready: {e}"})
        return

    # Register the `trainable` module in the kernel's sys.modules so cells can
    # `from trainable import log, configure_dashboard`. Silent + no-history so
    # it doesn't show up as cell input/output or bump execution counters.
    try:
        kc.execute(_SDK_PREAMBLE, silent=True, store_history=False)
    except Exception as e:
        _emit({"type": "warn", "error": f"preamble: {e}"})

    _emit({"type": "ready"})

    # Map jupyter msg_id -> our cell_id so we can route iopub messages.
    msg_to_cell = {}
    exec_counts = {}

    async def read_stdin():
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except Exception as e:
                _emit({"type": "warn", "error": f"bad-cmd: {e}"})
                continue
            action = cmd.get("action")
            if action == "execute":
                cell_id = cmd.get("cell_id")
                code = cmd.get("code", "")
                try:
                    msg_id = kc.execute(code, store_history=True)
                except Exception as e:
                    _emit({"type": "cell_error",
                           "payload": {"cell_id": cell_id,
                                       "ename": type(e).__name__,
                                       "evalue": str(e),
                                       "traceback": traceback.format_exc().splitlines()}})
                    _emit({"type": "cell_completed",
                           "payload": {"cell_id": cell_id, "exec_count": None}})
                    continue
                msg_to_cell[msg_id] = cell_id
                _emit({"type": "cell_started", "payload": {"cell_id": cell_id}})
            elif action == "interrupt":
                try:
                    await km.interrupt_kernel()
                except Exception as e:
                    _emit({"type": "warn", "error": f"interrupt: {e}"})
            elif action == "shutdown":
                try:
                    await km.shutdown_kernel(now=True)
                except Exception:
                    pass
                return

    async def drain_iopub():
        while True:
            try:
                msg = await kc.get_iopub_msg()
            except Exception as e:
                _emit({"type": "warn", "error": f"iopub: {e}"})
                await asyncio.sleep(0.1)
                continue
            parent_id = (msg.get("parent_header") or {}).get("msg_id")
            cell_id = msg_to_cell.get(parent_id)
            if not cell_id:
                continue
            mtype = msg.get("msg_type")
            content = msg.get("content") or {}
            if mtype == "execute_input":
                ec = content.get("execution_count")
                if ec is not None:
                    exec_counts[cell_id] = ec
            elif mtype == "stream":
                _emit({"type": "cell_stream",
                       "payload": {"cell_id": cell_id,
                                   "name": content.get("name", "stdout"),
                                   "text": content.get("text", "")}})
            elif mtype in ("display_data", "execute_result"):
                _emit({"type": "cell_display",
                       "payload": {"cell_id": cell_id,
                                   "data": content.get("data", {}),
                                   "metadata": content.get("metadata", {})}})
            elif mtype == "error":
                _emit({"type": "cell_error",
                       "payload": {"cell_id": cell_id,
                                   "ename": content.get("ename", ""),
                                   "evalue": content.get("evalue", ""),
                                   "traceback": content.get("traceback", [])}})
            elif mtype == "status" and content.get("execution_state") == "idle":
                _emit({"type": "cell_completed",
                       "payload": {"cell_id": cell_id,
                                   "exec_count": exec_counts.pop(cell_id, None)}})
                msg_to_cell.pop(parent_id, None)

    async def drain_shell():
        while True:
            try:
                await kc.get_shell_msg()
            except Exception:
                await asyncio.sleep(0.1)

    try:
        await asyncio.gather(read_stdin(), drain_iopub(), drain_shell())
    finally:
        try:
            kc.stop_channels()
        except Exception:
            pass

asyncio.run(main())
"""

# Materialize the proxy source with the SDK preamble embedded as a Python
# string literal. repr() gives us correct escaping regardless of the
# preamble's contents. The session-less default (used by tests) embeds the
# placeholder-only preamble; live spawns pass session_id to bake the path
# helpers in correctly.
KERNEL_PROXY_SCRIPT = _KERNEL_PROXY_SCRIPT_TEMPLATE.replace(
    "__SDK_PREAMBLE_LITERAL__", repr(SDK_PREAMBLE)
)


def build_kernel_proxy_script(session_id: str) -> str:
    """Per-session kernel proxy. The SDK preamble is rebuilt per session so
    rich helpers like log_image write to /data/sessions/{session_id}/figures/."""
    return _KERNEL_PROXY_SCRIPT_TEMPLATE.replace(
        "__SDK_PREAMBLE_LITERAL__", repr(build_sdk_preamble(session_id))
    )


@dataclass
class CellExecution:
    """Per-cell in-flight state: when it started + waiters to resolve."""

    started_at: float
    future: asyncio.Future
    notebook_name: str
    had_error: bool = False


@dataclass
class KernelHandle:
    session_id: str
    sandbox: modal.Sandbox
    state: str = "starting"  # starting | idle | busy | dead
    last_active: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    exec_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reader_task: Optional[asyncio.Task] = None
    stderr_task: Optional[asyncio.Task] = None
    pending: dict[str, CellExecution] = field(default_factory=dict)


class KernelManager:
    def __init__(self) -> None:
        self._kernels: dict[str, KernelHandle] = {}
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._reaper: Optional[asyncio.Task] = None

    def start_idle_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(REAPER_POLL_S)
                now = time.time()
                for sid, h in list(self._kernels.items()):
                    if h.state == "dead":
                        self._kernels.pop(sid, None)
                        continue
                    if h.state == "busy":
                        continue
                    if now - h.last_active > KERNEL_IDLE_TIMEOUT_S:
                        logger.info("Reaping idle kernel for session %s", sid)
                        await self.shutdown(sid)
                    elif now - h.created_at > KERNEL_MAX_LIFETIME_S:
                        logger.info("Reaping long-lived kernel for session %s", sid)
                        await self.shutdown(sid)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.exception("reaper: %s", e)

    def _create_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._create_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._create_locks[session_id] = lock
        return lock

    async def _spawn(self, session_id: str) -> KernelHandle:
        await broadcaster.publish(
            session_id,
            {"type": "notebook.kernel.state", "data": {"state": "starting"}},
        )
        # Lay down /sessions/{sid}/src/__init__.py so `workdir=` below has a
        # real directory on the first kernel of a fresh session. Same pattern
        # as run_code in sandbox.py.
        try:
            await ensure_session_workspace(session_id)
        except Exception as e:
            logger.debug("kernel workspace ensure skipped: %s", e)
        sb = await modal.Sandbox.create.aio(
            "python",
            "-u",
            "-c",
            build_kernel_proxy_script(session_id),
            image=get_image(),
            volumes={"/data": get_volume()},
            timeout=KERNEL_MAX_LIFETIME_S,
            # Anchor cwd to the session workspace so notebook cells that use
            # relative paths (`open("data/x.parquet")`) land on the volume.
            workdir=f"/data/sessions/{session_id}",
            app=await get_app(),
        )
        handle = KernelHandle(session_id=session_id, sandbox=sb)
        self._kernels[session_id] = handle
        handle.reader_task = asyncio.create_task(self._reader_loop(handle))
        handle.stderr_task = asyncio.create_task(self._stderr_loop(handle))
        return handle

    async def get_or_create(self, session_id: str) -> KernelHandle:
        h = self._kernels.get(session_id)
        if h and h.state != "dead":
            return h
        async with self._create_lock(session_id):
            h = self._kernels.get(session_id)
            if h and h.state != "dead":
                return h
            h = await self._spawn(session_id)
        try:
            await asyncio.wait_for(h.ready_event.wait(), timeout=KERNEL_READY_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("Kernel for session %s never signaled ready", session_id)
            await self.shutdown(session_id)
            raise RuntimeError("Kernel failed to start within timeout")
        return h

    async def _reader_loop(self, handle: KernelHandle) -> None:
        sid = handle.session_id
        buf = ""
        try:
            async for chunk in handle.sandbox.stdout:
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("non-JSON proxy stdout: %s", line[:200])
                        continue
                    try:
                        await self._dispatch(handle, event)
                    except Exception as e:
                        logger.exception("dispatch error: %s", e)
        except Exception as e:
            logger.exception("reader loop: %s", e)
        finally:
            if handle.state != "dead":
                handle.state = "dead"
                await broadcaster.publish(
                    sid,
                    {"type": "notebook.kernel.state", "data": {"state": "dead"}},
                )

    async def _stderr_loop(self, handle: KernelHandle) -> None:
        try:
            async for chunk in handle.sandbox.stderr:
                logger.debug("kernel[%s] stderr: %s", handle.session_id, chunk[:500])
        except Exception:
            pass

    async def _dispatch(self, handle: KernelHandle, event: dict) -> None:
        sid = handle.session_id
        etype = event.get("type")
        payload = event.get("payload") or {}

        if etype == "ready":
            handle.state = "idle"
            handle.ready_event.set()
            handle.last_active = time.time()
            await broadcaster.publish(
                sid, {"type": "notebook.kernel.state", "data": {"state": "idle"}}
            )
            return
        if etype in ("fatal", "warn"):
            logger.warning("kernel[%s] %s: %s", sid, etype, event.get("error"))
            if etype == "fatal":
                handle.state = "dead"
                await broadcaster.publish(
                    sid, {"type": "notebook.kernel.state", "data": {"state": "dead"}}
                )
            return

        cell_id = payload.get("cell_id")
        pending = handle.pending.get(cell_id) if cell_id else None
        # Events arriving before execute() registers are rare — fall back to
        # the default notebook so routing still happens correctly.
        notebook_name = (
            pending.notebook_name if pending else notebook_store.DEFAULT_NOTEBOOK_NAME
        )
        routed_payload = {**payload, "notebook_name": notebook_name}

        if etype == "cell_started":
            handle.state = "busy"
            handle.last_active = time.time()
            if cell_id and cell_id not in handle.pending:
                handle.pending[cell_id] = CellExecution(
                    started_at=time.time(),
                    future=asyncio.get_running_loop().create_future(),
                    notebook_name=notebook_name,
                )
            await broadcaster.publish(
                sid, {"type": "notebook.kernel.state", "data": {"state": "busy"}}
            )
            await notebook_store.on_cell_event(
                sid, notebook_name, "cell_started", routed_payload
            )
            await broadcaster.publish(
                sid, {"type": "notebook.cell.started", "data": routed_payload}
            )
        elif etype == "cell_stream":
            capped_text = _cap_text(payload.get("text", ""), MAX_STREAM_CHARS, "stream")
            routed_payload = {**routed_payload, "text": capped_text}
            await notebook_store.on_cell_event(
                sid, notebook_name, "cell_stream", routed_payload
            )
            await broadcaster.publish(
                sid, {"type": "notebook.cell.stream", "data": routed_payload}
            )
        elif etype == "cell_display":
            routed_payload = {
                **routed_payload,
                "data": _cap_display_data(payload.get("data", {})),
            }
            await notebook_store.on_cell_event(
                sid, notebook_name, "cell_display", routed_payload
            )
            await broadcaster.publish(
                sid, {"type": "notebook.cell.display", "data": routed_payload}
            )
        elif etype == "cell_error":
            if pending:
                pending.had_error = True
            await notebook_store.on_cell_event(
                sid, notebook_name, "cell_error", routed_payload
            )
            await broadcaster.publish(
                sid, {"type": "notebook.cell.error", "data": routed_payload}
            )
        elif etype == "cell_completed":
            handle.state = "idle"
            handle.last_active = time.time()
            duration_ms = None
            had_error = False
            pending = handle.pending.pop(cell_id, None) if cell_id else None
            if pending:
                duration_ms = int((time.time() - pending.started_at) * 1000)
                had_error = pending.had_error
                notebook_name = pending.notebook_name
            routed_payload = {
                **routed_payload,
                "notebook_name": notebook_name,
                "duration_ms": duration_ms,
                "had_error": had_error,
            }
            await notebook_store.on_cell_event(
                sid, notebook_name, "cell_completed", routed_payload
            )
            await broadcaster.publish(
                sid, {"type": "notebook.cell.completed", "data": routed_payload}
            )
            await broadcaster.publish(
                sid, {"type": "notebook.kernel.state", "data": {"state": "idle"}}
            )
            if pending and not pending.future.done():
                pending.future.set_result(
                    {
                        "cell_id": cell_id,
                        "notebook_name": notebook_name,
                        "exec_count": payload.get("exec_count"),
                        "duration_ms": duration_ms,
                        "had_error": had_error,
                    }
                )

    async def execute(
        self,
        session_id: str,
        cell_id: str,
        code: str,
        notebook_name: str = notebook_store.DEFAULT_NOTEBOOK_NAME,
    ) -> None:
        handle = await self.get_or_create(session_id)
        cmd = json.dumps({"action": "execute", "cell_id": cell_id, "code": code}) + "\n"
        async with handle.exec_lock:
            # Pre-register so dispatch can route cell_started / outputs to the
            # correct notebook regardless of which event arrives first.
            handle.pending.setdefault(
                cell_id,
                CellExecution(
                    started_at=time.time(),
                    future=asyncio.get_running_loop().create_future(),
                    notebook_name=notebook_name,
                ),
            )
            # Modal's _StreamWriter: `write` is a synchronous buffer call,
            # only `drain` is awaited. Neither is a blueprint method.
            handle.sandbox.stdin.write(cmd.encode("utf-8"))
            await handle.sandbox.stdin.drain.aio()
            handle.last_active = time.time()

    async def execute_and_wait(
        self,
        session_id: str,
        cell_id: str,
        code: str,
        notebook_name: str = notebook_store.DEFAULT_NOTEBOOK_NAME,
        timeout: float = 600.0,
    ) -> dict:
        """Submit a cell and await its completion."""
        handle = await self.get_or_create(session_id)
        pending = handle.pending.setdefault(
            cell_id,
            CellExecution(
                started_at=time.time(),
                future=asyncio.get_running_loop().create_future(),
                notebook_name=notebook_name,
            ),
        )
        await self.execute(session_id, cell_id, code, notebook_name=notebook_name)
        try:
            return await asyncio.wait_for(pending.future, timeout=timeout)
        except asyncio.TimeoutError:
            handle.pending.pop(cell_id, None)
            raise

    async def interrupt(self, session_id: str) -> bool:
        handle = self._kernels.get(session_id)
        if not handle or handle.state == "dead":
            return False
        cmd = json.dumps({"action": "interrupt"}) + "\n"
        try:
            handle.sandbox.stdin.write(cmd.encode("utf-8"))
            await handle.sandbox.stdin.drain.aio()
        except Exception as e:
            logger.warning("interrupt write failed: %s", e)
            return False
        return True

    async def shutdown(self, session_id: str) -> bool:
        handle = self._kernels.pop(session_id, None)
        if not handle:
            return False
        handle.state = "dead"
        # Ask the proxy to shut down gracefully, then terminate the sandbox.
        try:
            handle.sandbox.stdin.write(
                (json.dumps({"action": "shutdown"}) + "\n").encode("utf-8")
            )
            await handle.sandbox.stdin.drain.aio()
        except Exception:
            pass
        try:
            await handle.sandbox.terminate.aio()
        except Exception as e:
            logger.debug("terminate: %s", e)
        for task in (handle.reader_task, handle.stderr_task):
            if task and not task.done():
                task.cancel()
        await broadcaster.publish(
            session_id, {"type": "notebook.kernel.state", "data": {"state": "dead"}}
        )
        return True

    async def shutdown_all(self) -> None:
        for sid in list(self._kernels.keys()):
            try:
                await self.shutdown(sid)
            except Exception as e:
                logger.warning("shutdown_all %s: %s", sid, e)
        if self._reaper and not self._reaper.done():
            self._reaper.cancel()

    def status(self, session_id: str) -> dict:
        h = self._kernels.get(session_id)
        if not h:
            return {"state": "dead", "last_active": None}
        return {
            "state": h.state,
            "last_active": h.last_active,
            "created_at": h.created_at,
        }


kernel_manager = KernelManager()
