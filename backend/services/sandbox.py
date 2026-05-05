"""Modal Sandbox integration for isolated Python code execution."""

from __future__ import annotations

import asyncio
import logging
import time

import modal

from config import settings
from observability import sandbox_span
from services.broadcaster import broadcaster
from services.metrics import (
    parse_stdout_line,
    persist_and_publish,
    publish_chart_config,
)
from services.usage import record_sandbox_usage
from services.volume import get_volume

logger = logging.getLogger(__name__)

_app = None
_image = None


async def get_app():
    """Return the shared Modal App (lazy init, async).

    Modal's sync `App.lookup` issues an `AsyncUsageWarning` when called from
    an event-loop context (all our callers). Use the `.aio` blueprint to
    stay on the async path.
    """
    global _app
    if _app is None:
        _app = await modal.App.lookup.aio(
            settings.modal_app_name, create_if_missing=True
        )
    return _app


# Backwards-compatible alias — callers inside this module still use the underscore name.
_get_app = get_app


# SDK injected at the top of every sandbox execution.
# Creates a `trainable` module so agent code can do:
#   from trainable import log, configure_dashboard
#
# Used two ways:
#   1. execute_code scripts — string-concatenated ahead of user code in run_code()
#   2. notebook kernels    — sent to ipykernel as a silent preamble cell at boot
#      (see kernel_manager.py)
SDK_PREAMBLE = """\
import types, json, sys
_m = types.ModuleType('trainable')
_json = json
def _log(step, metrics, run=None):
    p = {"step": int(step), "metrics": {k: float(v) for k, v in metrics.items()}}
    if run: p["run"] = str(run)
    print(_json.dumps(p), flush=True)
def _cfg(charts):
    print(_json.dumps({"chart_config": {"charts": charts}}), flush=True)
_m.log = _log
_m.configure_dashboard = _cfg
sys.modules['trainable'] = _m
del _m, types
"""


def get_image():
    """Return the shared Modal Image (lazy init). Reused by the notebook kernel."""
    global _image
    if _image is None:
        img = (
            modal.Image.debian_slim(python_version="3.11")
            .pip_install(
                "pandas",
                "numpy",
                "matplotlib",
                "seaborn",
                "scikit-learn",
                "xgboost",
                "lightgbm",
                "pyarrow",
                "openpyxl",
                "duckdb",
                "imbalanced-learn",
                "optuna",
                "category_encoders",
                "pandera",
                "shap",
                "statsmodels",
                "ipykernel",
                "jupyter_client",
                "pypdf",
            )
            .pip_install(
                "torch",
                "torchvision",
                "torchaudio",
                index_url="https://download.pytorch.org/whl/cpu",
            )
            .pip_install(
                "tensorflow-cpu",
            )
        )
        # Mount the bundled skills directory at /skills so scripts referenced
        # from a SKILL.md are reachable via execute_code (e.g.
        # `sys.path.insert(0, "/skills/eda-report/scripts")`).
        from pathlib import Path

        skills_dir = Path(__file__).parent.parent / "skills"
        if skills_dir.exists():
            img = img.add_local_dir(str(skills_dir), "/skills", copy=True)
        _image = img
    return _image


_get_image = get_image


async def run_code(
    code: str,
    session_id: str,
    stage: str = None,
    gpu: str = None,
    timeout: int | None = None,
    agent_type: str | None = None,
    agent_id: str | None = None,
) -> dict:
    """Execute Python code in a Modal Sandbox with data volume mounted at /data."""

    effective_timeout = timeout or settings.sandbox_timeout
    logger.info(
        "Creating sandbox for session %s (%d chars, gpu=%s, timeout=%ds)",
        session_id,
        len(code),
        gpu,
        effective_timeout,
    )

    full_code = SDK_PREAMBLE + code

    # Wrap the entire sandbox lifecycle in an OTel span so trace UIs can show
    # how long the Modal call took, what stage it served, and which GPU was
    # provisioned. The span is opened manually (not via `with`) so the
    # following streaming-stdout block doesn't have to be re-indented.
    _sandbox_cm = sandbox_span(
        session_id=session_id, stage=stage, gpu=gpu, agent_type=agent_type
    )
    _sandbox_span_obj = _sandbox_cm.__enter__()
    try:
        _sandbox_span_obj.set_attribute("sandbox.code_chars", len(code))
        _sandbox_span_obj.set_attribute("sandbox.timeout_s", effective_timeout)
    except Exception:
        pass

    sandbox_failed = False
    try:
        started = time.monotonic()
        sb = await modal.Sandbox.create.aio(
            "python",
            "-u",
            "-c",
            full_code,
            image=_get_image(),
            volumes={"/data": get_volume()},
            gpu=gpu,
            timeout=effective_timeout,
            app=await _get_app(),
        )

        logger.info("Running code in sandbox for session %s", session_id)

        stdout_parts = []
        stderr_parts = []
        line_buffer = ""

        async def _dispatch(parsed: dict):
            if parsed["type"] == "metrics":
                await persist_and_publish(session_id, stage, parsed["items"])
            elif parsed["type"] == "chart_config":
                await publish_chart_config(session_id, parsed["config"])

        async def _drain_stderr():
            async for chunk in sb.stderr:
                stderr_parts.append(chunk)

        stderr_task = asyncio.create_task(_drain_stderr())

        try:
            async for chunk in sb.stdout:
                stdout_parts.append(chunk)
                await broadcaster.publish(
                    session_id,
                    {
                        "type": "code_output",
                        "data": {"stream": "stdout", "text": chunk},
                    },
                )

                if stage:
                    line_buffer += chunk
                    lines = line_buffer.split("\n")
                    line_buffer = lines[-1]
                    for line in lines[:-1]:
                        parsed = parse_stdout_line(line)
                        if parsed:
                            try:
                                await _dispatch(parsed)
                            except Exception as e:
                                logger.warning("Metric/config publish error: %s", e)

            if stage and line_buffer.strip():
                parsed = parse_stdout_line(line_buffer)
                if parsed:
                    try:
                        await _dispatch(parsed)
                    except Exception as e:
                        logger.warning("Metric/config flush error: %s", e)
        finally:
            # Ensure the stderr drainer doesn't outlive the sandbox call.
            if not stderr_task.done():
                stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception) as e:
                if not isinstance(e, asyncio.CancelledError):
                    logger.debug("stderr drainer exited with: %s", e)

        await sb.wait.aio()

        elapsed = time.monotonic() - started
        result = {
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
            "returncode": sb.returncode,
        }

        logger.info(
            "Sandbox done. exit=%d stdout=%dB stderr=%dB elapsed=%.2fs",
            sb.returncode,
            len(result["stdout"]),
            len(result["stderr"]),
            elapsed,
        )

        try:
            await record_sandbox_usage(
                session_id=session_id,
                agent_type=agent_type or stage,
                agent_id=agent_id,
                seconds=elapsed,
                gpu=gpu,
                is_error=sb.returncode != 0,
                extra={"stage": stage, "code_chars": len(code)},
            )
        except Exception as e:
            logger.debug("record_sandbox_usage failed: %s", e)

        try:
            _sandbox_span_obj.set_attribute("sandbox.elapsed_s", elapsed)
            _sandbox_span_obj.set_attribute("sandbox.returncode", int(sb.returncode))
            if sb.returncode != 0:
                _sandbox_span_obj.set_attribute("error", True)
                sandbox_failed = True
        except Exception:
            pass

        return result
    except Exception:
        sandbox_failed = True
        try:
            _sandbox_span_obj.set_attribute("error", True)
        except Exception:
            pass
        raise
    finally:
        try:
            import sys as _sys

            if sandbox_failed:
                _sandbox_cm.__exit__(*_sys.exc_info())
            else:
                _sandbox_cm.__exit__(None, None, None)
        except Exception:
            pass
