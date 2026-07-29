"""RunPod implementation of SandboxProvider — serverless code-runner.

Each canonical GPU tier gets a scale-to-zero serverless endpoint running
the trainable worker image (see docker/runpod-worker/). A "sandbox" is one
job on that endpoint: the worker subprocess-runs `python -u -c <code>` on
the mounted network volume and yields stdout/stderr lines, which we drain
via the /stream API and re-expose as async iterators so run_code() sees
the exact same handle shape as Modal's.

Pods were rejected for this concern: RunPod has no pod-logs API and a
per-execution pod create/pull/terminate costs minutes for a 10-second
script; serverless queues the job, streams partial output and scales to
zero, with a 60s idle window keeping workers warm across consecutive
agent tool calls.
"""

from __future__ import annotations

import asyncio
import logging

from services.compute.base import SandboxProvider, SandboxTimeoutError
from services.compute.runpod_provider.bootstrap import ensure_runner_endpoint
from services.compute.runpod_provider.client import get_client

logger = logging.getLogger(__name__)

_STREAM_POLL_S = 0.5
_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}

# Consecutive /stream failures tolerated before the job is declared lost.
# A single transient network blip (429, 5xx, read timeout) must not
# condemn a healthy job to FAILED/returncode -9.
_MAX_STREAM_FAILURES = 5

# /run payloads are capped around 10 MB; generated code is normally KBs.
_MAX_CODE_BYTES = 5 * 1024 * 1024


class RunPodJobHandle:
    """Adapts one serverless job to the SandboxHandle protocol."""

    def __init__(self, endpoint_id: str, job_id: str):
        self._endpoint_id = endpoint_id
        self._job_id = job_id
        self._stdout_q: asyncio.Queue = asyncio.Queue()
        self._stderr_q: asyncio.Queue = asyncio.Queue()
        self.returncode: int | None = None
        self._final_status: str | None = None
        self._poller = asyncio.create_task(self._poll())
        self.stdout = self._drain(self._stdout_q)
        self.stderr = self._drain(self._stderr_q)

    async def _drain(self, q: asyncio.Queue):
        while True:
            item = await q.get()
            if item is None:
                return
            yield item

    def _consume(self, output) -> None:
        """Route one worker-yielded item into the right queue."""
        if not isinstance(output, dict):
            return
        if "returncode" in output:
            try:
                self.returncode = int(output["returncode"])
            except (TypeError, ValueError):
                pass
            return
        text = output.get("text")
        if text is None:
            return
        if output.get("stream") == "stderr":
            self._stderr_q.put_nowait(text)
        else:
            self._stdout_q.put_nowait(text)

    async def _poll(self) -> None:
        client = get_client()
        failures = 0
        try:
            while True:
                try:
                    data = await client.stream(self._endpoint_id, self._job_id)
                except Exception as e:
                    failures += 1
                    if failures <= _MAX_STREAM_FAILURES:
                        logger.info(
                            "[runpod] job %s stream poll error (%d/%d): %s",
                            self._job_id,
                            failures,
                            _MAX_STREAM_FAILURES,
                            e,
                        )
                        await asyncio.sleep(_STREAM_POLL_S)
                        continue
                    raise
                failures = 0
                for item in data.get("stream") or []:
                    self._consume(item.get("output"))
                status = data.get("status")
                if status in _TERMINAL_STATUSES:
                    self._final_status = status
                    break
                await asyncio.sleep(_STREAM_POLL_S)
        except Exception as e:
            logger.warning("[runpod] job %s stream poll failed: %s", self._job_id, e)
            self._final_status = self._final_status or "FAILED"
            self._stderr_q.put_nowait(f"[runpod] output stream lost: {e}\n")
        finally:
            if self.returncode is None:
                # The worker only skips the final returncode yield when the
                # process was killed (timeout/cancel) or the worker crashed.
                if self._final_status == "COMPLETED":
                    self.returncode = 0
                elif self._final_status == "TIMED_OUT":
                    self.returncode = 124
                else:
                    self.returncode = -9
            self._stdout_q.put_nowait(None)
            self._stderr_q.put_nowait(None)

    async def wait(self) -> int | None:
        await asyncio.shield(self._poller)
        if self._final_status == "TIMED_OUT":
            raise SandboxTimeoutError(
                f"RunPod job {self._job_id} hit its execution timeout"
            )
        return self.returncode

    async def terminate(self) -> None:
        try:
            await get_client().cancel(self._endpoint_id, self._job_id)
        except Exception as e:
            logger.debug("[runpod] cancel %s failed: %s", self._job_id, e)
        if not self._poller.done():
            self._poller.cancel()
            self._stdout_q.put_nowait(None)
            self._stderr_q.put_nowait(None)


class RunPodSandboxProvider(SandboxProvider):
    name = "runpod"

    async def create(
        self,
        *,
        code: str,
        session_id: str,
        gpu: str | None,
        timeout: int,
        workdir: str,
    ) -> RunPodJobHandle:
        if len(code.encode("utf-8")) > _MAX_CODE_BYTES:
            raise ValueError(
                "Generated code exceeds the RunPod job payload limit "
                f"({_MAX_CODE_BYTES // (1024 * 1024)} MB). Split the work "
                "into smaller execute-code calls."
            )
        endpoint_id = await ensure_runner_endpoint(gpu)
        job = await get_client().run(
            endpoint_id,
            {
                "input": {"code": code, "workdir": workdir},
                "policy": {"executionTimeout": int(timeout) * 1000},
            },
        )
        job_id = job.get("id")
        if not job_id:
            raise RuntimeError(f"RunPod /run returned no job id: {job}")
        logger.info(
            "[runpod] queued job %s on endpoint %s (gpu=%s, timeout=%ss)",
            job_id,
            endpoint_id,
            gpu or "cpu",
            timeout,
        )
        return RunPodJobHandle(endpoint_id, job_id)
