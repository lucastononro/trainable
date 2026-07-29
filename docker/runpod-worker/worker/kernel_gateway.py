"""Notebook kernel HTTP gateway — the RunPod counterpart of the Modal
stdin/stdout kernel proxy (backend/services/kernel_manager.py).

Runs inside a long-lived pod. Owns a real ipykernel via jupyter_client's
AsyncKernelManager and emits the exact same newline-JSON events the Modal
proxy writes to stdout — but into an in-memory ring buffer served over
HTTP, because RunPod pods have no stdin/stdout API:

    POST /cmd               body = one JSON command line
                            {action: execute|interrupt|shutdown, ...}
    GET  /events?cursor=N   long-poll ≤25s; returns {"events": [...],
                            "cursor": M} with events *after* N. Cursors
                            are monotonically increasing so a dropped
                            poll never loses events. The 25s cap stays
                            safely under the pod proxy's 100s connection
                            kill.
    GET  /health            liveness probe

All routes require the X-Gateway-Token header (env KERNEL_GATEWAY_TOKEN).
The trainable SDK preamble arrives base64-encoded via SDK_PREAMBLE_B64
and runs as a silent execute before any user cell.
"""

import asyncio
import base64
import json
import os
import sys
import traceback

from fastapi import FastAPI, Request, Response
from jupyter_client.manager import AsyncKernelManager

GATEWAY_PORT = 8081
LONG_POLL_S = 25.0
RING_MAX = 10_000

TOKEN = os.environ.get("KERNEL_GATEWAY_TOKEN", "")
WORKDIR = os.environ.get("KERNEL_WORKDIR") or "/data"

app = FastAPI()

_events: list[str] = []  # ring buffer of JSON event lines
_base_cursor = 0  # cursor of _events[0]
_events_cond: asyncio.Condition | None = None

_km: AsyncKernelManager | None = None
_kc = None
_msg_to_cell: dict = {}
_exec_counts: dict = {}


def _emit_sync(obj) -> None:
    """Append one event (called from the event loop only)."""
    global _base_cursor
    _events.append(json.dumps(obj))
    if len(_events) > RING_MAX:
        drop = len(_events) - RING_MAX
        del _events[:drop]
        _base_cursor += drop


async def _emit(obj) -> None:
    _emit_sync(obj)
    async with _events_cond:
        _events_cond.notify_all()


async def _start_kernel() -> None:
    global _km, _kc
    os.makedirs(WORKDIR, exist_ok=True)
    os.chdir(WORKDIR)
    _km = AsyncKernelManager(kernel_name="python3")
    await _km.start_kernel()
    _kc = _km.client()
    _kc.start_channels()
    try:
        await _kc.wait_for_ready(timeout=60)
    except Exception as e:
        await _emit({"type": "fatal", "error": f"kernel-not-ready: {e}"})
        return

    # Register the `trainable` module in the kernel's sys.modules so cells
    # can `from trainable import log, ...`. Silent + no-history so it
    # doesn't show up as cell output or bump execution counters.
    preamble_b64 = os.environ.get("SDK_PREAMBLE_B64", "")
    if preamble_b64:
        try:
            preamble = base64.b64decode(preamble_b64).decode("utf-8")
            _kc.execute(preamble, silent=True, store_history=False)
        except Exception as e:
            await _emit({"type": "warn", "error": f"preamble: {e}"})

    await _emit({"type": "ready"})
    asyncio.get_running_loop().create_task(_drain_iopub())
    asyncio.get_running_loop().create_task(_drain_shell())


async def _drain_iopub() -> None:
    while True:
        try:
            msg = await _kc.get_iopub_msg()
        except Exception as e:
            await _emit({"type": "warn", "error": f"iopub: {e}"})
            await asyncio.sleep(0.1)
            continue
        parent_id = (msg.get("parent_header") or {}).get("msg_id")
        cell_id = _msg_to_cell.get(parent_id)
        if not cell_id:
            continue
        mtype = msg.get("msg_type")
        content = msg.get("content") or {}
        if mtype == "execute_input":
            ec = content.get("execution_count")
            if ec is not None:
                _exec_counts[cell_id] = ec
        elif mtype == "stream":
            await _emit(
                {
                    "type": "cell_stream",
                    "payload": {
                        "cell_id": cell_id,
                        "name": content.get("name", "stdout"),
                        "text": content.get("text", ""),
                    },
                }
            )
        elif mtype in ("display_data", "execute_result"):
            await _emit(
                {
                    "type": "cell_display",
                    "payload": {
                        "cell_id": cell_id,
                        "data": content.get("data", {}),
                        "metadata": content.get("metadata", {}),
                    },
                }
            )
        elif mtype == "error":
            await _emit(
                {
                    "type": "cell_error",
                    "payload": {
                        "cell_id": cell_id,
                        "ename": content.get("ename", ""),
                        "evalue": content.get("evalue", ""),
                        "traceback": content.get("traceback", []),
                    },
                }
            )
        elif mtype == "status" and content.get("execution_state") == "idle":
            await _emit(
                {
                    "type": "cell_completed",
                    "payload": {
                        "cell_id": cell_id,
                        "exec_count": _exec_counts.pop(cell_id, None),
                    },
                }
            )
            _msg_to_cell.pop(parent_id, None)


async def _drain_shell() -> None:
    while True:
        try:
            await _kc.get_shell_msg()
        except Exception:
            await asyncio.sleep(0.1)


def _authorized(request: Request) -> bool:
    return TOKEN and request.headers.get("X-Gateway-Token") == TOKEN


@app.on_event("startup")
async def _startup() -> None:
    global _events_cond
    _events_cond = asyncio.Condition()
    asyncio.get_running_loop().create_task(_start_kernel())


@app.get("/health")
async def health(request: Request):
    if not _authorized(request):
        return Response(status_code=401)
    return {"ok": True, "events": _base_cursor + len(_events)}


@app.get("/events")
async def events(request: Request, cursor: int = 0):
    if not _authorized(request):
        return Response(status_code=401)

    def _slice(after: int) -> list[str]:
        start = max(after - _base_cursor, 0)
        return _events[start:]

    pending = _slice(cursor)
    if not pending:
        try:
            async with _events_cond:
                await asyncio.wait_for(
                    _events_cond.wait_for(lambda: bool(_slice(cursor))),
                    timeout=LONG_POLL_S,
                )
        except asyncio.TimeoutError:
            pass
        pending = _slice(cursor)
    return {"events": pending, "cursor": _base_cursor + len(_events)}


@app.post("/cmd")
async def cmd(request: Request):
    if not _authorized(request):
        return Response(status_code=401)
    try:
        body = json.loads((await request.body()).decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"bad-cmd: {e}"}

    action = body.get("action")
    if action == "execute":
        cell_id = body.get("cell_id")
        code = body.get("code", "")
        try:
            msg_id = _kc.execute(code, store_history=True)
        except Exception as e:
            await _emit(
                {
                    "type": "cell_error",
                    "payload": {
                        "cell_id": cell_id,
                        "ename": type(e).__name__,
                        "evalue": str(e),
                        "traceback": traceback.format_exc().splitlines(),
                    },
                }
            )
            await _emit(
                {
                    "type": "cell_completed",
                    "payload": {"cell_id": cell_id, "exec_count": None},
                }
            )
            return {"ok": False}
        _msg_to_cell[msg_id] = cell_id
        await _emit({"type": "cell_started", "payload": {"cell_id": cell_id}})
        return {"ok": True}
    if action == "interrupt":
        try:
            await _km.interrupt_kernel()
        except Exception as e:
            await _emit({"type": "warn", "error": f"interrupt: {e}"})
        return {"ok": True}
    if action == "shutdown":
        try:
            await _km.shutdown_kernel(now=True)
        except Exception:
            pass
        # The backend deletes the pod right after; exiting is best-effort.
        asyncio.get_running_loop().call_later(0.5, sys.exit, 0)
        return {"ok": True}
    return {"ok": False, "error": f"unknown action {action!r}"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=GATEWAY_PORT, log_level="warning")
