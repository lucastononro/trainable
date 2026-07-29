"""Tests for services/compute_allowance.py and the SandboxConfig allowance
schema — the shared resolver that both the execute-code handler and the
system-prompt renderer consume."""

import pytest
from pydantic import ValidationError

from config import settings
from schemas import CANONICAL_GPUS, SandboxConfig
from services.compute_allowance import (
    clamp_timeout,
    normalize_gpu,
    resolve_compute_allowance,
)


class TestNormalizeGpu:
    def test_canonical_passthrough(self):
        for label in CANONICAL_GPUS:
            assert normalize_gpu(label) == label

    def test_case_insensitive(self):
        assert normalize_gpu("h100") == "H100"
        assert normalize_gpu("CPU") == "cpu"
        assert normalize_gpu("a100-80gb") == "A100-80GB"

    def test_legacy_aliases(self):
        # The old frontend dropdown wrote 'A100' into project configs.
        assert normalize_gpu("A100") == "A100-40GB"
        assert normalize_gpu("none") == "cpu"

    def test_unknown_returns_none(self):
        assert normalize_gpu("B9000") is None
        assert normalize_gpu("") is None
        assert normalize_gpu(None) is None


class TestResolveAllowance:
    def test_absent_config_allows_cpu_only(self):
        allowance = resolve_compute_allowance(None)
        assert allowance.allowed_gpus == ["cpu"]
        assert allowance.max_timeout == settings.sandbox_timeout
        assert allowance.explicit is False

    def test_profile_gpus_implicitly_allowed(self):
        allowance = resolve_compute_allowance(
            {"default": {"gpu": None}, "training": {"gpu": "A10G"}}
        )
        assert allowance.allowed_gpus == ["cpu", "A10G"]
        assert allowance.explicit is False

    def test_explicit_list_unions_profiles_and_cpu(self):
        allowance = resolve_compute_allowance(
            {
                "training": {"gpu": "A10G"},
                "allowed_gpus": ["T4", "H100"],
            }
        )
        assert allowance.allowed_gpus == ["cpu", "T4", "A10G", "H100"]
        assert allowance.explicit is True

    def test_legacy_profile_label_normalized(self):
        allowance = resolve_compute_allowance({"training": {"gpu": "A100"}})
        assert "A100-40GB" in allowance.allowed_gpus

    def test_result_sorted_cost_ascending(self):
        allowance = resolve_compute_allowance(
            {"allowed_gpus": ["H100", "cpu", "L4", "T4"]}
        )
        assert allowance.allowed_gpus == ["cpu", "T4", "L4", "H100"]

    def test_max_timeout_defaults_to_largest_profile(self):
        allowance = resolve_compute_allowance(
            {"default": {"timeout": 300}, "training": {"timeout": 3600}}
        )
        assert allowance.max_timeout == 3600

    def test_max_timeout_never_below_settings_default(self):
        allowance = resolve_compute_allowance({"default": {"timeout": 60}})
        assert allowance.max_timeout == settings.sandbox_timeout

    def test_explicit_max_timeout_wins(self):
        allowance = resolve_compute_allowance(
            {"training": {"timeout": 3600}, "max_timeout": 900}
        )
        assert allowance.max_timeout == 900


class TestClampTimeout:
    def _allowance(self, max_timeout=1000):
        return resolve_compute_allowance({"max_timeout": max_timeout})

    def test_above_cap_clamped_down(self):
        assert clamp_timeout(5000, self._allowance()) == 1000

    def test_below_floor_raised_to_ten(self):
        assert clamp_timeout(1, self._allowance()) == 10

    def test_in_range_passthrough(self):
        assert clamp_timeout(500, self._allowance()) == 500

    def test_garbage_returns_none(self):
        assert clamp_timeout("soon", self._allowance()) is None
        assert clamp_timeout(None, self._allowance()) is None


class TestSandboxConfigSchema:
    def test_unknown_label_rejected(self):
        with pytest.raises(ValidationError, match="Unknown GPU label"):
            SandboxConfig(allowed_gpus=["T4", "GTX-9090"])

    def test_dedupe_preserves_order(self):
        cfg = SandboxConfig(allowed_gpus=["T4", "H100", "T4"])
        assert cfg.allowed_gpus == ["T4", "H100"]

    def test_empty_list_normalizes_to_none(self):
        assert SandboxConfig(allowed_gpus=[]).allowed_gpus is None

    def test_max_timeout_bounds(self):
        with pytest.raises(ValidationError):
            SandboxConfig(max_timeout=5)
        with pytest.raises(ValidationError):
            SandboxConfig(max_timeout=10_000)
        assert SandboxConfig(max_timeout=600).max_timeout == 600

    def test_old_shape_still_valid(self):
        cfg = SandboxConfig(
            default={"gpu": None, "timeout": 600},
            training={"gpu": "A10G", "timeout": 1800},
        )
        assert cfg.allowed_gpus is None
        assert cfg.max_timeout is None


class TestPromptRendering:
    def test_renders_allowed_labels_and_cap(self):
        from services.agent.runner import _format_compute_env

        block = _format_compute_env(
            {
                "default": {"gpu": None, "timeout": 600},
                "training": {"gpu": "A10G", "timeout": 1800},
                "allowed_gpus": ["T4"],
                "max_timeout": 3600,
            }
        )
        assert "`cpu`" in block
        assert "`T4`" in block
        assert "`A10G`" in block
        assert "`H100`" not in block
        assert "max 3600s" in block
        assert "heavy=True" in block

    def test_prices_omitted_gracefully(self, monkeypatch):
        import services.agent.runner as runner_mod

        monkeypatch.setattr(
            runner_mod,
            "_gpu_hourly_usd",
            lambda gpu: (_ for _ in ()).throw(RuntimeError("no rates")),
        )
        # A rate failure must not break prompt assembly.
        with pytest.raises(RuntimeError):
            runner_mod._gpu_hourly_usd("T4")
        monkeypatch.setattr(runner_mod, "_gpu_hourly_usd", lambda gpu: None)
        block = runner_mod._format_compute_env({})
        assert "$" not in block
        assert "`cpu`" in block
