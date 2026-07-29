"""Tests for the run-file skill handler (issue #85)."""

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

HANDLER_PATH = (
    Path(__file__).resolve().parents[1] / "skills" / "run-file" / "handler.py"
)


def _load_handler_module():
    spec = importlib.util.spec_from_file_location("run_file_handler", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler_mod():
    mod = _load_handler_module()
    mod.run_code = AsyncMock(
        return_value={"stdout": "ran ok", "stderr": "", "returncode": 0}
    )
    mod.write_to_volume = AsyncMock()
    mod.read_volume_file_async = AsyncMock(return_value=b"print('hi')\n")
    mod.detect_new_files = AsyncMock()
    return mod


class _PublishRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event_type, data, role=None, **kwargs):
        self.events.append({"type": event_type, "data": data, "role": role})

    def of_type(self, event_type):
        return [e for e in self.events if e["type"] == event_type]


CONFIG = {
    "default": {"gpu": None, "timeout": 600},
    "training": {"gpu": "A10G", "timeout": 1800},
    "max_timeout": 3600,
}


def _make_handler(mod, sandbox_config=None, session_id="sess-r1"):
    publish = _PublishRecorder()
    handler = mod.create_handler(
        session_id=session_id,
        stage="train",
        publish_fn=publish,
        sandbox_config=sandbox_config,
    )
    return handler, publish


class TestRunFile:
    @pytest.mark.asyncio
    async def test_happy_path_runs_via_runpy(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        result = await handler({"path": "src/loaders.py"})
        assert not result.get("is_error")
        assert result["content"][0]["text"] == "ran ok"

        code = handler_mod.run_code.await_args.args[0]
        assert "runpy.run_path('src/loaders.py', run_name='__main__')" in code
        kwargs = handler_mod.run_code.await_args.kwargs
        assert kwargs["gpu"] is None
        assert kwargs["timeout"] == 600

    @pytest.mark.asyncio
    async def test_audit_log_gets_one_liner_not_body(self, handler_mod):
        handler, publish = _make_handler(handler_mod, CONFIG)
        await handler({"path": "src/loaders.py"})

        # Exactly one volume write: the audit script. The module body is
        # never written by run-file (it already lives on disk).
        handler_mod.write_to_volume.assert_awaited_once()
        audit_code, audit_path = handler_mod.write_to_volume.await_args.args
        assert audit_path.startswith("/sessions/sess-r1/scripts/step_")
        assert "_run_src_loaders_py.py" in audit_path
        assert "runpy.run_path('src/loaders.py'" in audit_code
        assert "print('hi')" not in audit_code

        created = publish.of_type("file_created")
        assert any(e["data"]["path"] == audit_path for e in created)

    @pytest.mark.asyncio
    async def test_missing_file_errors_before_running(self, handler_mod):
        handler_mod.read_volume_file_async = AsyncMock(side_effect=FileNotFoundError)
        handler, _ = _make_handler(handler_mod, CONFIG)
        result = await handler({"path": "src/nope.py"})
        assert result["is_error"] is True
        assert "write-file" in result["content"][0]["text"]
        handler_mod.run_code.assert_not_awaited()
        handler_mod.write_to_volume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_path_rejected(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        result = await handler({"path": "/etc/passwd"})
        assert result["is_error"] is True
        handler_mod.run_code.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_heavy_uses_training_profile(self, handler_mod):
        handler, _ = _make_handler(handler_mod, CONFIG)
        await handler({"path": "src/training.py", "heavy": True})
        kwargs = handler_mod.run_code.await_args.kwargs
        assert kwargs["gpu"] == "A10G"
        assert kwargs["timeout"] == 1800

    @pytest.mark.asyncio
    async def test_owner_max_timeout_caps_profile(self, handler_mod):
        cfg = {"training": {"gpu": "A10G", "timeout": 1800}, "max_timeout": 100}
        handler, _ = _make_handler(handler_mod, cfg)
        await handler({"path": "src/training.py", "heavy": True})
        assert handler_mod.run_code.await_args.kwargs["timeout"] == 100

    @pytest.mark.asyncio
    async def test_nonzero_exit_surfaces_stderr(self, handler_mod):
        handler_mod.run_code = AsyncMock(
            return_value={"stdout": "", "stderr": "boom", "returncode": 1}
        )
        handler, _ = _make_handler(handler_mod, CONFIG)
        result = await handler({"path": "src/loaders.py"})
        text = result["content"][0]["text"]
        assert "Exit code 1" in text
        assert "boom" in text

    @pytest.mark.asyncio
    async def test_sandbox_exception_returns_tool_error(self, handler_mod):
        handler_mod.run_code = AsyncMock(side_effect=RuntimeError("kaput"))
        handler, publish = _make_handler(handler_mod, CONFIG)
        result = await handler({"path": "src/loaders.py"})
        assert result["is_error"] is True
        assert "kaput" in result["content"][0]["text"]
        assert len(publish.of_type("tool_end")) == 1
