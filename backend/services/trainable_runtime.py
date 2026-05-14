"""Trainable runtime SDK used in both Modal sandboxes and local exports."""

import json
import os
import pathlib
import re
import sys
import time
import types


_TABLE_ROW_LIMIT = 1000
_MODE = os.environ.get("TRAINABLE_RUNTIME_MODE", "local")
_SID = os.environ.get("TRAINABLE_SESSION_ID", "")
_VOL_ROOT = pathlib.Path(os.environ.get("TRAINABLE_VOLUME_ROOT", "/data"))

if _MODE == "sandbox":
    _OUT = _VOL_ROOT / "sessions" / _SID
else:
    _OUT = pathlib.Path(
        os.environ.get("TRAINABLE_LOCAL_OUT", "./trainable_out")
    ).resolve()
    _OUT.mkdir(parents=True, exist_ok=True)

_FIG_BASE = _OUT / "figures"
_HTML_BASE = _OUT / "html"
_METRICS_FILE = _OUT / "metrics.jsonl"
_LOG_FILE = _OUT / "log_events.jsonl"
_PUBLIC_API = (
    "log",
    "configure_dashboard",
    "log_image",
    "log_images",
    "log_figure",
    "log_table",
    "log_confusion_matrix",
    "show_html",
)


def _bootstrap_session_repo() -> None:
    if _MODE != "sandbox" or not _SID:
        return
    session_src = _VOL_ROOT / "sessions" / _SID / "src"
    try:
        session_src.mkdir(parents=True, exist_ok=True)
        init_py = session_src / "__init__.py"
        if not init_py.exists():
            init_py.write_text("")
        src = str(session_src)
        if src not in sys.path:
            sys.path.insert(0, src)
    except Exception:
        pass


def _safe_key(key) -> str:
    return re.sub(r"[^A-Za-z0-9_./-]", "_", str(key)).strip("/") or "log"


def _append_jsonl(path: pathlib.Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _save_image(img, dest_path: pathlib.Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(img, (str, bytes, os.PathLike)):
        src = os.fspath(img)
        if str(src) == str(dest_path):
            return
        with open(src, "rb") as r, open(dest_path, "wb") as w:
            w.write(r.read())
        return

    try:
        from PIL import Image as _PILImage  # type: ignore

        if isinstance(img, _PILImage.Image):
            img.convert("RGB").save(dest_path, format="PNG")
            return
    except Exception:
        pass

    try:
        import torch as _torch  # type: ignore

        if isinstance(img, _torch.Tensor):
            arr = img.detach().cpu().numpy()
            if (
                arr.ndim == 3
                and arr.shape[0] in (1, 3, 4)
                and arr.shape[2]
                not in (
                    1,
                    3,
                    4,
                )
            ):
                arr = arr.transpose(1, 2, 0)
            img = arr
    except Exception:
        pass

    try:
        import numpy as _np  # type: ignore

        if isinstance(img, _np.ndarray):
            from PIL import Image as _PILImage  # type: ignore

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


def _volume_path(path: pathlib.Path) -> str:
    try:
        return "/" + str(path.relative_to(_VOL_ROOT)).lstrip("/")
    except ValueError:
        return str(path)


def _artifact_path(path: pathlib.Path) -> str:
    if _MODE == "sandbox":
        return _volume_path(path)
    return str(path.relative_to(_OUT))


def _emit(envelope: dict) -> None:
    print(json.dumps(envelope), flush=True)


def _log_event(event_type, step, key, data, run=None):
    safe = _safe_key(key)
    if _MODE == "sandbox":
        payload = {"type": event_type, "step": int(step), "key": safe, "data": data}
        if run:
            payload["run"] = str(run)
        _emit({"log": payload})
        return

    payload = {"type": event_type, "step": int(step), "key": safe}
    payload.update(data)
    if run:
        payload["run"] = str(run)
    _append_jsonl(_LOG_FILE, payload)


def log(step, metrics, run=None):
    payload = {
        "step": int(step),
        "metrics": {k: float(v) for k, v in dict(metrics).items()},
    }
    if run:
        payload["run"] = str(run)
    if _MODE == "sandbox":
        _emit(payload)
        return
    payload["ts"] = time.time()
    _append_jsonl(_METRICS_FILE, payload)
    print(f"[trainable] step={payload['step']} {payload['metrics']}")


def configure_dashboard(charts):
    if _MODE == "sandbox":
        _emit({"chart_config": {"charts": charts}})
        return
    (_OUT / "dashboard.json").write_text(json.dumps({"charts": charts}, indent=2))


def log_image(step, key, image, caption=None, run=None):
    safe = _safe_key(key)
    dest = _FIG_BASE / safe / f"{int(step)}.png"
    _save_image(image, dest)
    item = {"path": _artifact_path(dest)}
    if caption:
        item["caption"] = str(caption)
    data = {"items": [item]} if _MODE == "sandbox" else item
    _log_event("image", step, key, data, run=run)


def log_images(step, key, images, captions=None, run=None):
    safe = _safe_key(key)
    items = []
    for i, img in enumerate(images):
        dest = _FIG_BASE / safe / f"{int(step)}_{i}.png"
        _save_image(img, dest)
        item = {"path": _artifact_path(dest)}
        if captions and i < len(captions) and captions[i] is not None:
            item["caption"] = str(captions[i])
        items.append(item)
    _log_event("image_grid", step, key, {"items": items}, run=run)


def log_figure(step, key, fig, run=None):
    safe = _safe_key(key)
    dest = _FIG_BASE / safe / f"{int(step)}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(str(dest), format="png", bbox_inches="tight", dpi=120)
    except Exception as e:
        raise TypeError("log_figure: object is not a matplotlib Figure (%s)" % e)
    item = {"path": _artifact_path(dest)}
    data = {"items": [item]} if _MODE == "sandbox" else item
    _log_event("image", step, key, data, run=run)


def log_table(step, key, columns, rows, run=None):
    cols = [str(c) for c in columns]
    all_rows = list(rows)
    norm = []
    for r in all_rows[:_TABLE_ROW_LIMIT]:
        row = list(r) if not isinstance(r, dict) else [r.get(c) for c in cols]
        norm.append(
            [
                None
                if v is None
                else (
                    float(v)
                    if isinstance(v, bool) is False and isinstance(v, (int, float))
                    else str(v)
                )
                for v in row
            ]
        )
    _log_event(
        "table",
        step,
        key,
        {
            "columns": cols,
            "rows": norm,
            "truncated": len(all_rows) > _TABLE_ROW_LIMIT,
        },
        run=run,
    )


def log_confusion_matrix(step, key, y_true, y_pred, labels=None, run=None):
    try:
        from sklearn.metrics import confusion_matrix as _cm  # type: ignore

        labs = (
            list(labels)
            if labels is not None
            else sorted(set(list(y_true) + list(y_pred)))
        )
        matrix = _cm(y_true, y_pred, labels=labs).tolist()
    except Exception:
        labs = (
            list(labels)
            if labels is not None
            else sorted(set(list(y_true) + list(y_pred)))
        )
        idx = {lab: i for i, lab in enumerate(labs)}
        matrix = [[0] * len(labs) for _ in labs]
        for t, p in zip(y_true, y_pred):
            if t in idx and p in idx:
                matrix[idx[t]][idx[p]] += 1
    _log_event(
        "confusion_matrix",
        step,
        key,
        {"labels": [str(lab) for lab in labs], "matrix": matrix},
        run=run,
    )


def show_html(html, *, title=None, key=None):
    name = _safe_key(key) if key else "artifact"
    dest = _HTML_BASE / f"{name}.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(html, str):
        dest.write_text(html, encoding="utf-8")
    elif hasattr(html, "to_html"):
        dest.write_text(html.to_html(), encoding="utf-8")
    else:
        dest.write_text(str(html), encoding="utf-8")
    if _MODE == "sandbox":
        data = {"title": title or name, "path": _volume_path(dest)}
        _emit({"log": {"type": "html", "step": 0, "key": name, "data": data}})


def _install_trainable_module() -> None:
    current = sys.modules.get(globals().get("__name__", ""))
    if current is None:
        current = types.ModuleType("trainable")
        for name in _PUBLIC_API:
            setattr(current, name, globals()[name])
    sys.modules["trainable"] = current


_bootstrap_session_repo()
_install_trainable_module()
