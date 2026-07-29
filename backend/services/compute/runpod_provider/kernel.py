"""RunPod implementation of KernelTransport — long-lived pod + HTTP gateway.

Serverless can't hold a kernel between requests, so each session's kernel
lives in a dedicated pod running `worker/kernel_gateway.py` (same
AsyncKernelManager logic as the Modal stdin/stdout proxy, behind a tiny
HTTP server on port 8081):

    POST /cmd               one JSON command (execute/interrupt/shutdown)
    GET  /events?cursor=N   long-poll ≤25s over a ring buffer of events
    GET  /health

We reach it through RunPod's pod proxy (https://{podId}-8081.proxy.
runpod.net). The proxy hard-kills connections at 100s, so the gateway
long-poll stays well under that and `events()` is cursor-based — a
dropped poll never loses events. All routes require the per-pod
X-Gateway-Token minted here and injected via env.

Kernels are CPU-only (same as the Modal path). The pod bills while the
kernel is alive; the kernel manager's idle reaper (15 min) bounds that.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets as _secrets

import httpx

from config import settings
from services.compute.runpod_provider.bootstrap import ensure_network_volume
from services.compute.runpod_provider.client import get_client

logger = logging.getLogger(__name__)

GATEWAY_PORT = 8081
_POLL_RETRY_S = 2.0
_EVENTS_TIMEOUT = httpx.Timeout(10.0, read=35.0)  # gateway long-poll is ≤25s


class RunPodKernelTransport:
    def __init__(self, pod_id: str, token: str):
        self._pod_id = pod_id
        self._token = token
        self._base = f"https://{pod_id}-{GATEWAY_PORT}.proxy.runpod.net"
        self._http = httpx.AsyncClient(
            headers={"X-Gateway-Token": token}, timeout=_EVENTS_TIMEOUT
        )
        self._terminated = False

    async def send(self, line: str) -> None:
        resp = await self._http.post(
            f"{self._base}/cmd",
            content=line.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()

    async def events(self):
        cursor = 0
        while not self._terminated:
            try:
                resp = await self._http.get(
                    f"{self._base}/events", params={"cursor": cursor}
                )
                if resp.status_code != 200:
                    # Pod still booting / image pulling — the kernel
                    # manager's ready timeout bounds how long we retry.
                    await asyncio.sleep(_POLL_RETRY_S)
                    continue
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                if self._terminated:
                    return
                await asyncio.sleep(_POLL_RETRY_S)
                continue
            cursor = data.get("cursor", cursor)
            for line in data.get("events") or []:
                yield line

    async def terminate(self) -> None:
        self._terminated = True
        try:
            await get_client().delete_pod(self._pod_id)
        except Exception as e:
            logger.warning("[runpod] pod delete %s failed: %s", self._pod_id, e)
        try:
            await self._http.aclose()
        except Exception:
            pass


async def create_runpod_kernel_transport(session_id: str) -> RunPodKernelTransport:
    from services.sandbox import build_sdk_preamble

    token = _secrets.token_urlsafe(24)
    preamble_b64 = base64.b64encode(
        build_sdk_preamble(session_id).encode("utf-8")
    ).decode("ascii")
    volume_id = await ensure_network_volume()

    pod = await get_client().create_pod(
        {
            "name": f"trainable-kernel-{session_id[:12]}",
            "imageName": settings.runpod_worker_image,
            "cloudType": "SECURE",
            "computeType": "CPU",
            "instanceIds": ["cpu3c-2-8"],
            "containerDiskInGb": 20,
            "networkVolumeId": volume_id,
            "volumeMountPath": "/data",
            "dataCenterIds": [settings.runpod_datacenter_id],
            "ports": [f"{GATEWAY_PORT}/http"],
            "dockerStartCmd": ["python", "-m", "worker.kernel_gateway"],
            "env": {
                "TRAINABLE_ROLE": "kernel",
                "KERNEL_GATEWAY_TOKEN": token,
                "SDK_PREAMBLE_B64": preamble_b64,
                "SESSION_ID": session_id,
                "KERNEL_WORKDIR": f"/data/sessions/{session_id}",
            },
        }
    )
    pod_id = pod.get("id")
    if not pod_id:
        raise RuntimeError(f"RunPod pod create returned no id: {pod}")
    logger.info("[runpod] kernel pod %s created for session %s", pod_id, session_id)
    return RunPodKernelTransport(pod_id, token)
