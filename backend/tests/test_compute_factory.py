"""Tests for services/compute — provider selection and validation."""

import pytest

import services.compute as compute
from config import settings


@pytest.fixture(autouse=True)
def _reset_factory():
    """Each test starts with fresh singletons and default settings."""
    compute._sandbox_provider = None
    compute._storage = None
    compute._serving = None
    yield
    compute._sandbox_provider = None
    compute._storage = None
    compute._serving = None


def _set_runpod_creds(monkeypatch):
    monkeypatch.setattr(settings, "runpod_api_key", "rpa_test")
    monkeypatch.setattr(settings, "runpod_s3_access_key_id", "user_test")
    monkeypatch.setattr(settings, "runpod_s3_secret_access_key", "rps_test")


class TestProviderSelection:
    def test_default_is_modal(self):
        assert settings.compute_provider == "modal"
        assert compute.get_sandbox_provider().name == "modal"
        assert compute.get_storage().name == "modal"
        assert compute.get_serving_backend().name == "modal"

    def test_runpod_selected(self, monkeypatch):
        monkeypatch.setattr(settings, "compute_provider", "runpod")
        _set_runpod_creds(monkeypatch)
        assert compute.get_sandbox_provider().name == "runpod"
        assert compute.get_storage().name == "runpod"
        assert compute.get_serving_backend().name == "runpod"

    def test_switch_rebuilds_singletons(self, monkeypatch):
        assert compute.get_sandbox_provider().name == "modal"
        monkeypatch.setattr(settings, "compute_provider", "runpod")
        _set_runpod_creds(monkeypatch)
        assert compute.get_sandbox_provider().name == "runpod"

    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "compute_provider", "banana")
        with pytest.raises(RuntimeError, match="not supported"):
            compute.get_sandbox_provider()

    def test_runpod_without_creds_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "compute_provider", "runpod")
        monkeypatch.setattr(settings, "runpod_api_key", "")
        with pytest.raises(RuntimeError, match="RUNPOD_API_KEY"):
            compute.get_sandbox_provider()

    def test_runpod_missing_s3_keys_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "compute_provider", "runpod")
        monkeypatch.setattr(settings, "runpod_api_key", "rpa_test")
        monkeypatch.setattr(settings, "runpod_s3_access_key_id", "")
        monkeypatch.setattr(settings, "runpod_s3_secret_access_key", "")
        with pytest.raises(RuntimeError, match="RUNPOD_S3_ACCESS_KEY_ID"):
            compute.get_storage()


class TestKernelReadyTimeout:
    def test_modal_timeout(self):
        assert compute.kernel_ready_timeout_s() == 120

    def test_runpod_timeout(self, monkeypatch):
        monkeypatch.setattr(settings, "compute_provider", "runpod")
        _set_runpod_creds(monkeypatch)
        assert compute.kernel_ready_timeout_s() == 600


class TestGpuMapping:
    def test_all_canonical_labels_mapped(self):
        from services.compute.runpod_provider.gpu import RUNPOD_GPU_TYPES

        assert set(RUNPOD_GPU_TYPES) == {
            "cpu",
            "T4",
            "L4",
            "A10G",
            "A100-40GB",
            "A100-80GB",
            "H100",
        }

    def test_unknown_label_falls_back_to_cpu(self):
        from services.compute.runpod_provider.gpu import gpu_type_ids

        assert gpu_type_ids("B9000-MEGA") == []
        assert gpu_type_ids(None) == []

    def test_labels_match_billing_rates(self):
        """Every canonical label must have a runpod rate in sandbox.yml so
        agent GPU choices bill correctly."""
        from services.compute.runpod_provider.gpu import RUNPOD_GPU_TYPES
        from services.usage import _resolve_compute_rate

        for label in RUNPOD_GPU_TYPES:
            assert _resolve_compute_rate("runpod", label) > 0, label
