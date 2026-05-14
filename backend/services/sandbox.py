"""Modal Sandbox integration for isolated Python code execution."""

from __future__ import annotations

import asyncio
import logging
import textwrap
import time
from importlib import resources

import modal

from config import settings
from observability import sandbox_span
from services.broadcaster import broadcaster
from services.metrics import (
    parse_stdout_line,
    persist_and_publish,
    persist_and_publish_log_event,
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


def _read_trainable_runtime() -> str:
    return (
        resources.files("services")
        .joinpath("trainable_runtime.py")
        .read_text(encoding="utf-8")
    )


_TRAINABLE_RUNTIME_SOURCE = _read_trainable_runtime()
_TRAINABLE_RUNTIME_PREAMBLE_SOURCE = textwrap.indent(_TRAINABLE_RUNTIME_SOURCE, "    ")

# SDK injected at the top of every sandbox execution. The public API lives in
# services/trainable_runtime.py; this preamble only selects the sandbox sink and
# then executes that same runtime source.
SDK_PREAMBLE_TEMPLATE = f"""\
import os as _trn_os
_SID = "__SESSION_ID__"
_VOL_ROOT = "/data"
_TRAINABLE_ENV_KEYS = (
    "TRAINABLE_RUNTIME_MODE",
    "TRAINABLE_SESSION_ID",
    "TRAINABLE_VOLUME_ROOT",
)
_TRAINABLE_OLD_ENV = {{key: _trn_os.environ.get(key) for key in _TRAINABLE_ENV_KEYS}}
try:
    _trn_os.environ["TRAINABLE_RUNTIME_MODE"] = "sandbox"
    _trn_os.environ["TRAINABLE_SESSION_ID"] = _SID
    _trn_os.environ["TRAINABLE_VOLUME_ROOT"] = _VOL_ROOT
{_TRAINABLE_RUNTIME_PREAMBLE_SOURCE}
finally:
    for _trn_key, _trn_value in _TRAINABLE_OLD_ENV.items():
        if _trn_value is None:
            _trn_os.environ.pop(_trn_key, None)
        else:
            _trn_os.environ[_trn_key] = _trn_value
"""


def build_sdk_preamble(session_id: str) -> str:
    return SDK_PREAMBLE_TEMPLATE.replace("__SESSION_ID__", session_id)


# Back-compat alias: kernel_manager.py and tests import this constant by name.
SDK_PREAMBLE = build_sdk_preamble("")


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

    full_code = build_sdk_preamble(session_id) + code

    # Wrap the entire sandbox lifecycle in an OTel span so trace UIs can show
    # how long the Modal call took, what stage it served, and which GPU was
    # provisioned.
    with sandbox_span(
        session_id=session_id, stage=stage, gpu=gpu, agent_type=agent_type
    ) as _sandbox_span_obj:
        try:
            _sandbox_span_obj.set_attribute("sandbox.code_chars", len(code))
            _sandbox_span_obj.set_attribute("sandbox.timeout_s", effective_timeout)
        except Exception:
            pass

        try:
            # Reload the volume so this sandbox observes writes from the previous
            # one (e.g. an agent module written one execute_code call ago). The
            # post-run reload in detect_new_files only refreshes the backend view.
            # Also lay down /sessions/{sid}/src/__init__.py so `workdir=` below
            # has a real directory to anchor to on the first sandbox of a session.
            try:
                from services.volume import (
                    ensure_session_workspace as _ensure_ws,
                    reload_volume_async as _reload_vol,
                )

                await _ensure_ws(session_id)
                await _reload_vol()
            except Exception as e:
                logger.debug("pre-sandbox volume bootstrap skipped: %s", e)

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
                # Anchor the process cwd inside the session workspace so relative
                # file IO (open("data/x.parquet"), pd.to_parquet("models/m.pkl"))
                # lands on the volume instead of /root.
                workdir=f"/data/sessions/{session_id}",
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
                elif parsed["type"] == "log_event":
                    await persist_and_publish_log_event(
                        session_id, stage, parsed["event"]
                    )

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
                _sandbox_span_obj.set_attribute(
                    "sandbox.returncode", int(sb.returncode)
                )
                if sb.returncode != 0:
                    _sandbox_span_obj.set_attribute("error", True)
            except Exception:
                pass

            return result
        except Exception:
            try:
                _sandbox_span_obj.set_attribute("error", True)
            except Exception:
                pass
            raise
