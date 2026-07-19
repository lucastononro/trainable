"""Tests for the RunPod sandbox provider — job streaming and run_code
integration. No network: the RunPod client is replaced by a canned fake,
mirroring how modal tests monkeypatch modal.Sandbox.create."""

from unittest.mock import AsyncMock

import pytest

import services.compute as compute
from config import settings
from services.compute.base import SandboxTimeoutError
from services.compute.runpod_provider import sandbox as rp_sandbox


class FakeRunPodClient:
    """Serves a scripted sequence of /stream responses, then /status."""

    def __init__(self, stream_responses):
        self._responses = list(stream_responses)
        self.run_calls = []
        self.cancel_calls = []

    async def run(self, endpoint_id, payload):
        self.run_calls.append((endpoint_id, payload))
        return {"id": "job-1", "status": "IN_QUEUE"}

    async def stream(self, endpoint_id, job_id):
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    async def status(self, endpoint_id, job_id):
        return {"status": self._responses[-1]["status"]}

    async def cancel(self, endpoint_id, job_id):
        self.cancel_calls.append(job_id)
        return {"status": "CANCELLED"}


def _completed_stream(lines_then_rc=(("stdout", "hello\n"), ("stderr", "warn\n"))):
    items = [{"output": {"stream": name, "text": text}} for name, text in lines_then_rc]
    items.append({"output": {"returncode": 0}})
    return [
        {"status": "IN_PROGRESS", "stream": items[:1]},
        {"status": "COMPLETED", "stream": items[1:]},
    ]


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(rp_sandbox, "_STREAM_POLL_S", 0.001)


class TestRunPodJobHandle:
    @pytest.mark.asyncio
    async def test_streams_stdout_stderr_and_returncode(self, monkeypatch):
        fake = FakeRunPodClient(_completed_stream())
        monkeypatch.setattr(rp_sandbox, "get_client", lambda: fake)

        handle = rp_sandbox.RunPodJobHandle("ep-1", "job-1")
        stdout = [chunk async for chunk in handle.stdout]
        stderr = [chunk async for chunk in handle.stderr]
        rc = await handle.wait()

        assert stdout == ["hello\n"]
        assert stderr == ["warn\n"]
        assert rc == 0
        assert handle.returncode == 0

    @pytest.mark.asyncio
    async def test_timed_out_raises_sandbox_timeout(self, monkeypatch):
        fake = FakeRunPodClient([{"status": "TIMED_OUT", "stream": []}])
        monkeypatch.setattr(rp_sandbox, "get_client", lambda: fake)

        handle = rp_sandbox.RunPodJobHandle("ep-1", "job-1")
        async for _ in handle.stdout:
            pass
        with pytest.raises(SandboxTimeoutError):
            await handle.wait()
        assert handle.returncode == 124

    @pytest.mark.asyncio
    async def test_failed_without_returncode_maps_to_nonzero(self, monkeypatch):
        fake = FakeRunPodClient([{"status": "FAILED", "stream": []}])
        monkeypatch.setattr(rp_sandbox, "get_client", lambda: fake)

        handle = rp_sandbox.RunPodJobHandle("ep-1", "job-1")
        async for _ in handle.stdout:
            pass
        rc = await handle.wait()
        assert rc != 0

    @pytest.mark.asyncio
    async def test_terminate_cancels_job(self, monkeypatch):
        fake = FakeRunPodClient([{"status": "IN_PROGRESS", "stream": []}])
        monkeypatch.setattr(rp_sandbox, "get_client", lambda: fake)

        handle = rp_sandbox.RunPodJobHandle("ep-1", "job-1")
        await handle.terminate()
        assert fake.cancel_calls == ["job-1"]


class TestRunPodProviderCreate:
    @pytest.mark.asyncio
    async def test_create_sends_timeout_policy_and_workdir(self, monkeypatch):
        fake = FakeRunPodClient(_completed_stream())
        monkeypatch.setattr(rp_sandbox, "get_client", lambda: fake)
        monkeypatch.setattr(
            rp_sandbox, "ensure_runner_endpoint", AsyncMock(return_value="ep-t4")
        )

        provider = rp_sandbox.RunPodSandboxProvider()
        handle = await provider.create(
            code="print('hi')",
            session_id="sess-1",
            gpu="T4",
            timeout=300,
            workdir="/data/sessions/sess-1",
        )
        await handle.wait()

        endpoint_id, payload = fake.run_calls[0]
        assert endpoint_id == "ep-t4"
        assert payload["input"]["workdir"] == "/data/sessions/sess-1"
        assert payload["policy"]["executionTimeout"] == 300_000
        rp_sandbox.ensure_runner_endpoint.assert_awaited_once_with("T4")

    @pytest.mark.asyncio
    async def test_oversized_code_rejected(self, monkeypatch):
        monkeypatch.setattr(
            rp_sandbox, "ensure_runner_endpoint", AsyncMock(return_value="ep")
        )
        provider = rp_sandbox.RunPodSandboxProvider()
        with pytest.raises(ValueError, match="payload limit"):
            await provider.create(
                code="x" * (rp_sandbox._MAX_CODE_BYTES + 1),
                session_id="s",
                gpu=None,
                timeout=60,
                workdir="/data",
            )


class TestRunCodeOnRunPod:
    @pytest.mark.asyncio
    async def test_run_code_records_runpod_usage(self, monkeypatch):
        """End-to-end through services.sandbox.run_code with the runpod
        provider active: stdout/stderr collected, usage recorded with
        provider='runpod' and the gpu actually used."""
        import services.sandbox as sandbox_mod
        import services.volume as vol_mod

        monkeypatch.setattr(settings, "compute_provider", "runpod")
        monkeypatch.setattr(settings, "runpod_api_key", "rpa_test")
        monkeypatch.setattr(settings, "runpod_s3_access_key_id", "u")
        monkeypatch.setattr(settings, "runpod_s3_secret_access_key", "s")
        compute._sandbox_provider = None

        fake = FakeRunPodClient(_completed_stream())
        monkeypatch.setattr(rp_sandbox, "get_client", lambda: fake)
        monkeypatch.setattr(
            rp_sandbox, "ensure_runner_endpoint", AsyncMock(return_value="ep-1")
        )

        # Block the pre-sandbox volume bootstrap (would hit boto3).
        monkeypatch.setattr(vol_mod, "ensure_session_workspace", AsyncMock())
        monkeypatch.setattr(vol_mod, "reload_volume_async", AsyncMock())

        usage = AsyncMock()
        monkeypatch.setattr(sandbox_mod, "record_sandbox_usage", usage)

        try:
            result = await sandbox_mod.run_code(
                "print('hi')", session_id="sess-rp", gpu="L4", timeout=120
            )
        finally:
            compute._sandbox_provider = None

        assert result["returncode"] == 0
        assert result["stdout"] == "hello\n"
        assert result["stderr"] == "warn\n"
        kwargs = usage.await_args.kwargs
        assert kwargs["provider"] == "runpod"
        assert kwargs["gpu"] == "L4"
