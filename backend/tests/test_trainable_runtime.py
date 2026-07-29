"""Tests for services/trainable_runtime.py (local mode).

The runtime module reads its configuration from env vars at import time, so
each test loads it fresh via importlib with TRAINABLE_LOCAL_OUT pointed at a
tmp dir.
"""

import importlib.util
import json
import pathlib

_RUNTIME_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "services" / "trainable_runtime.py"
)


def _load_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAINABLE_RUNTIME_MODE", "local")
    monkeypatch.setenv("TRAINABLE_LOCAL_OUT", str(tmp_path / "out"))
    spec = importlib.util.spec_from_file_location(
        "trainable_runtime_under_test", _RUNTIME_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _last_event(tmp_path):
    lines = (tmp_path / "out" / "log_events.jsonl").read_text().splitlines()
    return json.loads(lines[-1])


def test_log_confusion_matrix_accepts_generators(tmp_path, monkeypatch):
    """Regression (Greptile P1 on PR #87): label inference must not exhaust
    generator inputs — y_true/y_pred are materialized once up front, so the
    matrix is computed from the real values, not empty sequences."""
    rt = _load_runtime(tmp_path, monkeypatch)

    rt.log_confusion_matrix(0, "cm", iter([0, 1, 1, 0, 1]), iter([0, 1, 0, 0, 1]))

    payload = _last_event(tmp_path)
    assert payload["type"] == "confusion_matrix"
    assert payload["labels"] == ["0", "1"]
    assert payload["matrix"] == [[2, 0], [1, 2]]


def test_log_confusion_matrix_explicit_labels_with_generators(tmp_path, monkeypatch):
    rt = _load_runtime(tmp_path, monkeypatch)

    rt.log_confusion_matrix(
        0, "cm", iter(["b", "a", "b"]), iter(["b", "b", "a"]), labels=["a", "b"]
    )

    payload = _last_event(tmp_path)
    assert payload["labels"] == ["a", "b"]
    assert payload["matrix"] == [[0, 1], [1, 1]]
