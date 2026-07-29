"""Tests for the RunPod kernel transport and worker gateway script."""

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.compute.runpod_provider import kernel as rp_kernel


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    """Scripted GET responses for /events, records POSTs to /cmd."""

    def __init__(self, get_responses):
        self._gets = list(get_responses)
        self.posts = []

    async def get(self, url, params=None):
        if self._gets:
            return self._gets.pop(0)
        return FakeResponse(payload={"events": [], "cursor": params["cursor"]})

    async def post(self, url, content=None, headers=None):
        self.posts.append((url, content))
        return FakeResponse()

    async def aclose(self):
        pass


class TestTransportEvents:
    @pytest.mark.asyncio
    async def test_events_yield_lines_and_advance_cursor(self):
        transport = RunPodTransportFactory.build(
            FakeHttp(
                [
                    FakeResponse(
                        payload={"events": ['{"type": "ready"}'], "cursor": 1}
                    ),
                    FakeResponse(
                        payload={
                            "events": ['{"type": "cell_started"}', '{"type": "x"}'],
                            "cursor": 3,
                        }
                    ),
                ]
            )
        )
        seen = []
        async for line in transport.events():
            seen.append(json.loads(line)["type"])
            if len(seen) == 3:
                transport._terminated = True
        assert seen == ["ready", "cell_started", "x"]

    @pytest.mark.asyncio
    async def test_boot_errors_are_retried_not_fatal(self, monkeypatch):
        monkeypatch.setattr(rp_kernel, "_POLL_RETRY_S", 0.001)
        transport = RunPodTransportFactory.build(
            FakeHttp(
                [
                    FakeResponse(status_code=502),  # pod still booting
                    FakeResponse(
                        payload={"events": ['{"type": "ready"}'], "cursor": 1}
                    ),
                ]
            )
        )
        got = []
        async for line in transport.events():
            got.append(line)
            transport._terminated = True
        assert got == ['{"type": "ready"}']

    @pytest.mark.asyncio
    async def test_send_posts_json_line(self):
        http = FakeHttp([])
        transport = RunPodTransportFactory.build(http)
        await transport.send('{"action": "interrupt"}')
        url, content = http.posts[0]
        assert url.endswith("/cmd")
        assert content == b'{"action": "interrupt"}'

    @pytest.mark.asyncio
    async def test_terminate_deletes_pod(self, monkeypatch):
        client = AsyncMock()
        monkeypatch.setattr(rp_kernel, "get_client", lambda: client)
        transport = RunPodTransportFactory.build(FakeHttp([]))
        await transport.terminate()
        client.delete_pod.assert_awaited_once_with("pod-1")


class RunPodTransportFactory:
    @staticmethod
    def build(http) -> rp_kernel.RunPodKernelTransport:
        transport = rp_kernel.RunPodKernelTransport.__new__(
            rp_kernel.RunPodKernelTransport
        )
        transport._pod_id = "pod-1"
        transport._token = "tok"
        transport._base = "https://pod-1-8081.proxy.runpod.net"
        transport._http = http
        transport._terminated = False
        return transport


class TestCreateTransport:
    @pytest.mark.asyncio
    async def test_pod_payload_shape(self, monkeypatch):
        client = AsyncMock()
        client.create_pod = AsyncMock(return_value={"id": "pod-77"})
        monkeypatch.setattr(rp_kernel, "get_client", lambda: client)
        monkeypatch.setattr(
            rp_kernel, "ensure_network_volume", AsyncMock(return_value="vol-1")
        )

        transport = await rp_kernel.create_runpod_kernel_transport("sess-abc")
        assert transport._pod_id == "pod-77"

        payload = client.create_pod.await_args.args[0]
        assert payload["volumeMountPath"] == "/data"
        assert payload["networkVolumeId"] == "vol-1"
        assert payload["ports"] == ["8081/http"]
        assert payload["env"]["TRAINABLE_ROLE"] == "kernel"
        assert payload["env"]["SESSION_ID"] == "sess-abc"
        assert payload["env"]["KERNEL_GATEWAY_TOKEN"]
        assert payload["env"]["SDK_PREAMBLE_B64"]
        assert payload["dockerStartCmd"] == ["python", "-m", "worker.kernel_gateway"]
        await transport._http.aclose()


class TestWorkerScripts:
    """The worker files ship inside the docker image — they must at least
    parse (they can't be imported here: runpod/fastapi deps + top-level
    serverless start)."""

    WORKER_DIR = (
        Path(__file__).resolve().parents[2] / "docker" / "runpod-worker" / "worker"
    )

    def test_worker_scripts_parse(self):
        for name in ("runner_handler.py", "kernel_gateway.py", "serving_launcher.py"):
            source = (self.WORKER_DIR / name).read_text()
            ast.parse(source, filename=name)

    def test_gateway_long_poll_under_proxy_limit(self):
        source = (self.WORKER_DIR / "kernel_gateway.py").read_text()
        # The RunPod pod proxy kills connections at 100s — the gateway's
        # long-poll must stay well under it.
        assert "LONG_POLL_S = 25.0" in source
