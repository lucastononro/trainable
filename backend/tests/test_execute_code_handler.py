"""Tests for the execute-code handler's agent-selectable compute — the
`gpu`/`timeout` args, allowance enforcement, and profile precedence."""

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

HANDLER_PATH = (
    Path(__file__).resolve().parents[1] / "skills" / "execute-code" / "handler.py"
)


def _load_handler_module():
    spec = importlib.util.spec_from_file_location("execute_code_handler", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler_mod(monkeypatch):
    mod = _load_handler_module()
    mod.run_code = AsyncMock(
        return_value={"stdout": "ok", "stderr": "", "returncode": 0}
    )
    mod.write_to_volume = AsyncMock()
    mod.detect_new_files = AsyncMock()
    return mod


def _make_handler(mod, sandbox_config=None):
    publish = AsyncMock()
    handler = mod.create_handler(
        session_id="sess-1",
        stage="train",
        publish_fn=publish,
        sandbox_config=sandbox_config,
    )
    return handler, publish


CONFIG = {
    "default": {"gpu": None, "timeout": 600},
    "training": {"gpu": "A10G", "timeout": 1800},
    "allowed_gpus": ["T4", "L4"],
    "max_timeout": 3600,
}


class TestGpuSelection:
    @pytest.mark.asyncio
    async def test_allowed_gpu_passes_through(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        result = await handler({"code": "print(1)", "gpu": "T4"})
        assert not result.get("is_error")
        assert handler_mod.run_code.await_args.kwargs["gpu"] == "T4"

    @pytest.mark.asyncio
    async def test_cpu_maps_to_none(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"code": "print(1)", "gpu": "cpu", "heavy": True})
        # Explicit cpu overrides the heavy profile's A10G.
        assert handler_mod.run_code.await_args.kwargs["gpu"] is None

    @pytest.mark.asyncio
    async def test_case_insensitive_label(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"code": "print(1)", "gpu": "t4"})
        assert handler_mod.run_code.await_args.kwargs["gpu"] == "T4"

    @pytest.mark.asyncio
    async def test_denied_gpu_returns_structured_error(self, handler_mod):
        handler, publish = _make_handler(handler_mod, CONFIG)
        result = await handler({"code": "print(1)", "gpu": "H100"})
        assert result["is_error"] is True
        text = result["content"][0]["text"]
        assert "H100" in text
        assert "cpu, T4, L4, A10G" in text
        handler_mod.run_code.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_profile_gpu_implicitly_allowed(self, handler_mod):
        # A10G comes from the training profile, not allowed_gpus.
        handler, _ = _make_handler(handler_mod, CONFIG)
        result = await handler({"code": "print(1)", "gpu": "A10G"})
        assert not result.get("is_error")
        assert handler_mod.run_code.await_args.kwargs["gpu"] == "A10G"


class TestPrecedence:
    @pytest.mark.asyncio
    async def test_explicit_gpu_beats_heavy_profile(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"code": "x", "heavy": True, "gpu": "L4"})
        assert handler_mod.run_code.await_args.kwargs["gpu"] == "L4"

    @pytest.mark.asyncio
    async def test_heavy_without_gpu_uses_training_profile(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"code": "x", "heavy": True})
        kwargs = handler_mod.run_code.await_args.kwargs
        assert kwargs["gpu"] == "A10G"
        assert kwargs["timeout"] == 1800

    @pytest.mark.asyncio
    async def test_neither_uses_default_profile(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"code": "x"})
        kwargs = handler_mod.run_code.await_args.kwargs
        assert kwargs["gpu"] is None
        assert kwargs["timeout"] == 600


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_above_cap_clamped(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"code": "x", "timeout": 99999})
        assert handler_mod.run_code.await_args.kwargs["timeout"] == 3600

    @pytest.mark.asyncio
    async def test_timeout_below_floor_raised(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"code": "x", "timeout": 3})
        assert handler_mod.run_code.await_args.kwargs["timeout"] == 10

    @pytest.mark.asyncio
    async def test_garbage_timeout_falls_back_to_profile(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"code": "x", "timeout": "soon"})
        assert handler_mod.run_code.await_args.kwargs["timeout"] == 600


class TestEventPayload:
    @pytest.mark.asyncio
    async def test_tool_start_carries_gpu_and_timeout(self, handler_mod):
        handler, publish = _make_handler(handler_mod, CONFIG)
        await handler({"code": "x", "gpu": "T4", "timeout": 120})
        start_calls = [c for c in publish.await_args_list if c.args[1] == "tool_start"]
        payload = start_calls[0].args[2]
        assert payload["gpu"] == "T4"
        assert payload["timeout"] == 120


class TestNoConfig:
    @pytest.mark.asyncio
    async def test_absent_config_only_cpu_allowed(self, handler_mod):
        handler, _ = _make_handler(handler_mod, None)
        result = await handler({"code": "x", "gpu": "T4"})
        assert result["is_error"] is True
        ok = await handler({"code": "x", "gpu": "cpu"})
        assert not ok.get("is_error")
