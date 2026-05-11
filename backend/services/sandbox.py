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


# SDK injected at the top of every sandbox execution.
# Creates a `trainable` module so agent code can do:
#   from trainable import log, configure_dashboard, log_image, log_table, ...
#
# Used two ways:
#   1. execute_code scripts — built per-session in run_code() with the session_id baked in
#   2. notebook kernels     — sent to ipykernel as a silent preamble cell at boot
#                             (see kernel_manager.py — also session-aware)
#
# `session_id` is interpolated into the file paths used by the rich helpers
# (log_image / log_images / log_figure) so binary artifacts land at
# /data/sessions/{sid}/figures/{key}/{step}.png and are addressable by the
# frontend via /files/raw?path=/sessions/{sid}/figures/{key}/{step}.png.
def build_sdk_preamble(session_id: str) -> str:
    return SDK_PREAMBLE_TEMPLATE.replace("__SESSION_ID__", session_id)


SDK_PREAMBLE_TEMPLATE = '''\
import types as _trn_types, json as _trn_json, sys as _trn_sys, os as _trn_os, re as _trn_re
_m = _trn_types.ModuleType("trainable")
_SID = "__SESSION_ID__"
# Volume mount inside the sandbox is /data; the frontend addresses files
# by their volume-relative path (no /data prefix), e.g. /sessions/{sid}/...
_VOL_ROOT = "/data"
_FIG_BASE = _trn_os.path.join(_VOL_ROOT, "sessions", _SID, "figures")
_TABLE_ROW_LIMIT = 1000  # truncated server-side too; UI never needs more

# --- Session repo bootstrap -------------------------------------------------
# Make /data/sessions/{sid}/src/ a proper Python package and put it FIRST on
# sys.path so the agent can `import data` / `from features import build_X`
# from any subsequent execute_code call or notebook cell.
#
# Why this matters: each execute_code call spawns a fresh sandbox whose cwd
# defaults to /root and whose sys.path doesn't include the session workspace.
# Without this, an agent that writes utils.py in one call hits ModuleNotFoundError
# on `import utils` in the next call. With this, the session feels like a repo.
_SESSION_SRC = _trn_os.path.join(_VOL_ROOT, "sessions", _SID, "src")
try:
    _trn_os.makedirs(_SESSION_SRC, exist_ok=True)
    _init_py = _trn_os.path.join(_SESSION_SRC, "__init__.py")
    if not _trn_os.path.exists(_init_py):
        with open(_init_py, "w") as _fh:
            _fh.write("")
    if _SESSION_SRC not in _trn_sys.path:
        _trn_sys.path.insert(0, _SESSION_SRC)
except Exception:
    # Best-effort: a misconfigured volume mount shouldn't kill the run.
    pass
# ---------------------------------------------------------------------------

def _safe_key(key):
    # `key` may include slashes (e.g. "val/predictions") — keep the slash
    # as a subdir separator but scrub anything else risky.
    return _trn_re.sub(r"[^A-Za-z0-9_./-]", "_", str(key)).strip("/") or "log"

def _vol_path(local_path):
    if local_path.startswith(_VOL_ROOT):
        return local_path[len(_VOL_ROOT):]
    return local_path

def _emit(envelope):
    print(_trn_json.dumps(envelope), flush=True)

def _save_image(img, dest_path):
    """Normalize an image-ish object to PNG at dest_path. Accepts:
    - str/PathLike: an existing file path (just copies if needed)
    - PIL.Image.Image
    - numpy.ndarray (HxW, HxWx3, HxWx4; uint8 or float-in-[0,1])
    - torch.Tensor (CxHxW or HxW or HxWxC)
    """
    _trn_os.makedirs(_trn_os.path.dirname(dest_path), exist_ok=True)
    # path passthrough
    if isinstance(img, (str, bytes, _trn_os.PathLike)):
        src = _trn_os.fspath(img)
        if src == dest_path:
            return
        with open(src, "rb") as r, open(dest_path, "wb") as w:
            w.write(r.read())
        return
    # PIL
    try:
        from PIL import Image as _PILImage
        if isinstance(img, _PILImage.Image):
            img.convert("RGB").save(dest_path, format="PNG")
            return
    except Exception:
        pass
    # torch — convert to numpy
    try:
        import torch as _torch
        if isinstance(img, _torch.Tensor):
            arr = img.detach().cpu().numpy()
            # CxHxW -> HxWxC
            if arr.ndim == 3 and arr.shape[0] in (1, 3, 4) and arr.shape[2] not in (1, 3, 4):
                arr = arr.transpose(1, 2, 0)
            img = arr
    except Exception:
        pass
    # numpy
    try:
        import numpy as _np
        if isinstance(img, _np.ndarray):
            from PIL import Image as _PILImage
            arr = img
            if arr.dtype != _np.uint8:
                a = arr.astype(_np.float32)
                if a.max() <= 1.0 + 1e-6:
                    a = a * 255.0
                arr = a.clip(0, 255).astype(_np.uint8)
            if arr.ndim == 2:
                _PILImage.fromarray(arr, mode="L").save(dest_path, format="PNG")
            elif arr.ndim == 3 and arr.shape[2] == 4:
                _PILImage.fromarray(arr, mode="RGBA").save(dest_path, format="PNG")
            else:
                _PILImage.fromarray(arr).convert("RGB").save(dest_path, format="PNG")
            return
    except Exception:
        pass
    raise TypeError("log_image: unsupported image type %r" % (type(img),))

def _log(step, metrics, run=None):
    p = {"step": int(step), "metrics": {k: float(v) for k, v in metrics.items()}}
    if run: p["run"] = str(run)
    _emit(p)

def _cfg(charts):
    _emit({"chart_config": {"charts": charts}})

def _log_event(event_type, step, key, data, run=None):
    payload = {"type": event_type, "step": int(step), "key": _safe_key(key), "data": data}
    if run: payload["run"] = str(run)
    _emit({"log": payload})

def _log_image(step, key, image, caption=None, run=None):
    safe = _safe_key(key)
    fname = "{}.png".format(int(step))
    dest = _trn_os.path.join(_FIG_BASE, safe, fname)
    _save_image(image, dest)
    item = {"path": _vol_path(dest)}
    if caption: item["caption"] = str(caption)
    _log_event("image", step, key, {"items": [item]}, run=run)

def _log_images(step, key, images, captions=None, run=None):
    safe = _safe_key(key)
    items = []
    for i, img in enumerate(images):
        dest = _trn_os.path.join(_FIG_BASE, safe, "{}_{}.png".format(int(step), i))
        _save_image(img, dest)
        item = {"path": _vol_path(dest)}
        if captions and i < len(captions) and captions[i] is not None:
            item["caption"] = str(captions[i])
        items.append(item)
    _log_event("image_grid", step, key, {"items": items}, run=run)

def _log_figure(step, key, fig, run=None):
    """Save a matplotlib Figure to PNG and emit an image event."""
    safe = _safe_key(key)
    dest = _trn_os.path.join(_FIG_BASE, safe, "{}.png".format(int(step)))
    _trn_os.makedirs(_trn_os.path.dirname(dest), exist_ok=True)
    try:
        fig.savefig(dest, format="png", bbox_inches="tight", dpi=120)
    except Exception as e:
        raise TypeError("log_figure: object is not a matplotlib Figure (%s)" % e)
    _log_event("image", step, key, {"items": [{"path": _vol_path(dest)}]}, run=run)

def _log_table(step, key, columns, rows, run=None):
    cols = [str(c) for c in columns]
    rs = list(rows)[:_TABLE_ROW_LIMIT]
    norm = []
    for r in rs:
        row = list(r) if not isinstance(r, dict) else [r.get(c) for c in cols]
        norm.append([(None if v is None else (float(v) if isinstance(v, bool) is False and isinstance(v, (int, float)) else str(v))) for v in row])
    _log_event(
        "table",
        step,
        key,
        {"columns": cols, "rows": norm, "truncated": len(list(rows)) > _TABLE_ROW_LIMIT},
        run=run,
    )

def _log_confusion_matrix(step, key, y_true, y_pred, labels=None, run=None):
    """Compute the confusion matrix server-side-free using sklearn if
    available; otherwise hand-roll it."""
    try:
        from sklearn.metrics import confusion_matrix as _cm
        import numpy as _np
        labs = list(labels) if labels is not None else sorted(set(list(y_true) + list(y_pred)))
        m = _cm(y_true, y_pred, labels=labs).tolist()
    except Exception:
        labs = list(labels) if labels is not None else sorted(set(list(y_true) + list(y_pred)))
        idx = {l: i for i, l in enumerate(labs)}
        m = [[0] * len(labs) for _ in labs]
        for t, p in zip(y_true, y_pred):
            if t in idx and p in idx:
                m[idx[t]][idx[p]] += 1
    _log_event(
        "confusion_matrix",
        step,
        key,
        {"labels": [str(l) for l in labs], "matrix": m},
        run=run,
    )

_m.log = _log
_m.configure_dashboard = _cfg
_m.log_image = _log_image
_m.log_images = _log_images
_m.log_figure = _log_figure
_m.log_table = _log_table
_m.log_confusion_matrix = _log_confusion_matrix
_trn_sys.modules["trainable"] = _m
del _m
'''


# Back-compat alias: kernel_manager.py and tests imported the constant by
# name. It now defaults to a no-session preamble (still works, but the
# rich helpers will write to /data/sessions/None/...). Kernel/code paths
# that know the session should call build_sdk_preamble(session_id).
SDK_PREAMBLE = SDK_PREAMBLE_TEMPLATE.replace("__SESSION_ID__", "")


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
                await persist_and_publish_log_event(session_id, stage, parsed["event"])

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
