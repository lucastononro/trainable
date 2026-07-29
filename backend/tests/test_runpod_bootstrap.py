"""Tests for the RunPod bootstrap — idempotent volume/template/endpoint
creation. No network: the RunPod client is a canned fake."""

import asyncio

import pytest

from config import settings
from services.compute.runpod_provider import bootstrap


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    bootstrap.reset_cache()
    monkeypatch.setattr(settings, "runpod_network_volume_id", "")
    yield
    bootstrap.reset_cache()


class FakeClient:
    """Empty account: every lookup misses, every create returns an id."""

    def __init__(self):
        self.created_endpoints = []

    async def list_network_volumes(self):
        return []

    async def create_network_volume(self, payload):
        return {"id": "vol-1"}

    async def list_templates(self):
        return []

    async def create_template(self, payload):
        return {"id": "tpl-1"}

    async def list_endpoints(self):
        return []

    async def create_endpoint(self, payload):
        self.created_endpoints.append(payload)
        return {"id": "ep-1"}


class TestEnsureRunnerEndpoint:
    @pytest.mark.asyncio
    async def test_cold_cache_creates_volume_template_endpoint(self, monkeypatch):
        """Regression: ensure_runner_endpoint used to hold the shared
        _lock across ensure_network_volume()/ensure_runner_template(),
        which acquire the same non-reentrant asyncio.Lock — a guaranteed
        deadlock on the first execution of a fresh setup. wait_for bounds
        the test so a regression fails instead of hanging."""
        fake = FakeClient()
        monkeypatch.setattr(bootstrap, "get_client", lambda: fake)

        endpoint_id = await asyncio.wait_for(
            bootstrap.ensure_runner_endpoint("T4"), timeout=5
        )

        assert endpoint_id == "ep-1"
        ep = fake.created_endpoints[0]
        assert ep["templateId"] == "tpl-1"
        assert ep["networkVolumeId"] == "vol-1"
        assert ep["gpuTypeIds"]  # T4 maps to a GPU pool

    @pytest.mark.asyncio
    async def test_cpu_endpoint_uses_cpu_compute(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(bootstrap, "get_client", lambda: fake)

        await asyncio.wait_for(bootstrap.ensure_runner_endpoint(None), timeout=5)

        ep = fake.created_endpoints[0]
        assert ep["computeType"] == "CPU"
        assert "gpuTypeIds" not in ep

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(bootstrap, "get_client", lambda: fake)

        await asyncio.wait_for(bootstrap.ensure_runner_endpoint("L4"), timeout=5)
        again = await asyncio.wait_for(
            bootstrap.ensure_runner_endpoint("L4"), timeout=5
        )

        assert again == "ep-1"
        assert len(fake.created_endpoints) == 1
