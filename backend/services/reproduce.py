"""Active reproduction of a run snapshot.

`snapshot.py` captures a *passive* record (dataset/code hashes + manifest).
This module turns it into a verified claim: re-execute the snapshot's
captured scripts in a fresh sandbox against the hashed data, collect the
metrics they emit, and diff them against the metrics recorded when the
run originally happened — flagging any drift.

Flow (see `reproduce_snapshot`):

1. Load the `RunSnapshot` row + its JSON manifest from the volume.
2. Re-hash the workspace's data/code files and compare with the manifest
   (input drift — the snapshot no longer describes what's on disk).
3. Replay the captured ``.py`` scripts in a Modal sandbox (same volume,
   same workdir, same SDK preamble as the original run). We deliberately
   pass ``stage=None`` so the replay's metric lines are *not* persisted
   into the session's metric history — the reproduction must observe,
   never contaminate, the original record.
4. Parse the metrics the replay printed to stdout and diff their final
   values against the final values recorded in the DB.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from sqlalchemy import select

from db import async_session
from models import Metric, RunSnapshot
from services.metrics import parse_metric_lines
from services.sandbox import run_code
from services.snapshot import _collect_files
from services.volume import (
    read_volume_file_async,
    reload_volume_async,
    write_to_volume,
)

logger = logging.getLogger(__name__)

# Relative + absolute tolerance for "same metric value". Exact replays of
# deterministic scripts produce identical floats; anything beyond this is
# reported as drift. Callers can widen it for intentionally-stochastic runs.
DEFAULT_TOLERANCE = 1e-6

_STDERR_TAIL_CHARS = 4000


class SnapshotNotFoundError(Exception):
    """No snapshot exists for the session."""


class ManifestUnavailableError(Exception):
    """The snapshot row exists but its manifest can't be read."""


class NoScriptsError(Exception):
    """The manifest captured no runnable .py scripts."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested directly)
# ---------------------------------------------------------------------------


def final_metric_values(items: list[dict]) -> dict[str, float]:
    """Collapse metric items ({step, name, value}, ...) to the final value
    per metric name. Items must be in emission order; the last occurrence
    of the highest step wins."""
    best: dict[str, tuple[int, float]] = {}
    for item in items:
        name = str(item["name"])
        step = int(item.get("step", 0))
        prev = best.get(name)
        if prev is None or step >= prev[0]:
            best[name] = (step, float(item["value"]))
    return {name: value for name, (_, value) in best.items()}


def diff_metrics(
    original: dict[str, float],
    reproduced: dict[str, float],
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict:
    """Diff final metric values from the original run vs the reproduction.

    Returns ``{"rows": [...], "summary": {...}, "drift_detected": bool}``.
    Row statuses: ``match`` (within tolerance), ``drift`` (value moved),
    ``missing`` (original metric the replay never emitted), ``new``
    (replay-only metric — informational, not drift).
    """
    rows: list[dict] = []
    summary = {"matched": 0, "drifted": 0, "missing": 0, "new": 0}

    for name in sorted(set(original) | set(reproduced)):
        orig = original.get(name)
        repro = reproduced.get(name)
        if orig is None:
            status = "new"
            abs_diff = rel_diff = None
        elif repro is None:
            status = "missing"
            abs_diff = rel_diff = None
        else:
            abs_diff = abs(repro - orig)
            denom = max(abs(orig), abs(repro))
            rel_diff = (abs_diff / denom) if denom else 0.0
            status = (
                "match"
                if math.isclose(orig, repro, rel_tol=tolerance, abs_tol=tolerance)
                else "drift"
            )
        key = {"match": "matched", "drift": "drifted"}.get(status, status)
        summary[key] += 1
        rows.append(
            {
                "name": name,
                "original": orig,
                "reproduced": repro,
                "abs_diff": abs_diff,
                "rel_diff": rel_diff,
                "status": status,
            }
        )

    # A metric that changed value or vanished is drift; a brand-new metric
    # is merely informational (the replay can't invalidate what it added).
    drift_detected = summary["drifted"] > 0 or summary["missing"] > 0
    return {"rows": rows, "summary": summary, "drift_detected": drift_detected}


def verify_inputs(
    manifest: dict, current_data: list[dict], current_code: list[dict]
) -> dict:
    """Compare the manifest's captured file hashes against the workspace's
    current files. Returns per-section verified flags + the changed files."""

    def _section(captured: list[dict], current: list[dict]) -> tuple[bool, list[dict]]:
        cur = {f["path"]: f["sha256"] for f in current}
        changed: list[dict] = []
        for f in captured:
            actual = cur.get(f["path"])
            if actual != f["sha256"]:
                changed.append(
                    {
                        "path": f["path"],
                        "expected_sha256": f["sha256"],
                        "actual_sha256": actual,  # None => file gone
                    }
                )
        return (not changed, changed)

    dataset_files = (manifest.get("dataset") or {}).get("files") or []
    code_files = (manifest.get("code") or {}).get("files") or []
    dataset_ok, dataset_changed = _section(dataset_files, current_data)
    code_ok, code_changed = _section(code_files, current_code)
    return {
        "dataset_verified": dataset_ok,
        "code_verified": code_ok,
        "changed_files": dataset_changed + code_changed,
    }


def select_replay_scripts(manifest: dict) -> list[str]:
    """Pick the captured .py scripts to replay, in manifest (path) order.

    Notebooks are skipped (no headless contract) and package ``__init__.py``
    stubs are skipped (imported by the scripts themselves, not entrypoints).
    """
    files = (manifest.get("code") or {}).get("files") or []
    return [
        f["path"]
        for f in files
        if f["path"].endswith(".py") and not f["path"].endswith("__init__.py")
    ]


def build_replay_code(script_paths: list[str]) -> str:
    """Runner executed inside the sandbox: run each captured script as
    ``__main__`` (volume mounts at /data). Metric lines the scripts print
    via the injected `trainable` SDK flow back on stdout; the first failing
    script aborts the replay with a nonzero exit."""
    sandbox_paths = [f"/data{p}" for p in script_paths]
    return (
        "import json as _rj, runpy as _rr, sys as _rs\n"
        f"_scripts = _rj.loads({json.dumps(json.dumps(sandbox_paths))})\n"
        "for _p in _scripts:\n"
        "    _rs.stderr.write('[reproduce] running %s\\n' % _p)\n"
        "    _rs.stderr.flush()\n"
        "    _rr.run_path(_p, run_name='__main__')\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _read_original_metrics(session_id: str) -> dict[str, float]:
    """Final recorded value per metric name for the session, in insertion
    order so `final_metric_values` sees the true emission sequence."""
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(Metric)
                    .where(Metric.session_id == session_id)
                    .order_by(Metric.id)
                )
            )
            .scalars()
            .all()
        )
    return final_metric_values(
        [{"step": r.step, "name": r.name, "value": r.value} for r in rows]
    )


async def reproduce_snapshot(
    session_id: str, tolerance: float = DEFAULT_TOLERANCE
) -> dict:
    """Re-execute a snapshot's captured scripts and diff resulting metrics.

    Raises `SnapshotNotFoundError` / `ManifestUnavailableError` /
    `NoScriptsError` for the router to map to HTTP errors.
    """
    async with async_session() as db:
        snap = (
            await db.execute(
                select(RunSnapshot).where(RunSnapshot.session_id == session_id)
            )
        ).scalar_one_or_none()
    if snap is None:
        raise SnapshotNotFoundError(f"No snapshot for session {session_id}")
    if not snap.manifest_uri:
        raise ManifestUnavailableError("Snapshot has no manifest on the volume")

    await reload_volume_async()
    try:
        manifest = json.loads(await read_volume_file_async(snap.manifest_uri))
    except Exception as e:
        raise ManifestUnavailableError(f"Could not read snapshot manifest: {e}") from e

    scripts = select_replay_scripts(manifest)
    if not scripts:
        raise NoScriptsError("Snapshot captured no .py scripts to replay")

    # Input integrity: does the workspace still match what was snapshotted?
    workspace = f"/sessions/{session_id}"
    current_data = await _collect_files(workspace, (".parquet", ".csv", ".feather"))
    current_code = await _collect_files(workspace, (".py", ".ipynb"))
    inputs = verify_inputs(manifest, current_data, current_code)

    original_metrics = await _read_original_metrics(session_id)

    # Replay. stage=None on purpose: the sandbox pipeline only persists
    # metric/log lines to the session when a stage is given, and a
    # reproduction must never write into the original run's history.
    result = await run_code(
        build_replay_code(scripts),
        session_id,
        stage=None,
        agent_type="reproduce",
    )
    returncode = result.get("returncode", -1)
    reproduced_metrics = final_metric_values(
        parse_metric_lines(result.get("stdout", ""))
    )

    diff = diff_metrics(original_metrics, reproduced_metrics, tolerance)
    if returncode != 0:
        status = "error"
    elif diff["drift_detected"]:
        status = "drift"
    else:
        status = "match"

    report = {
        "session_id": session_id,
        "snapshot_id": snap.id,
        "reproduced_at": datetime.now(timezone.utc).isoformat(),
        "tolerance": tolerance,
        "status": status,
        "inputs": inputs,
        "execution": {
            "returncode": returncode,
            "scripts": scripts,
            "stderr_tail": (result.get("stderr") or "")[-_STDERR_TAIL_CHARS:],
        },
        "metrics": {
            "original": original_metrics,
            "reproduced": reproduced_metrics,
            **diff,
        },
    }

    # Best-effort record next to snapshot.json — the volume stays the source
    # of truth for artifacts; failure to write must not fail the action.
    try:
        await write_to_volume(
            json.dumps(report, indent=2, default=str).encode("utf-8"),
            f"{workspace}/reproduce_report.json",
        )
    except Exception as e:
        logger.warning("Could not write reproduce report to volume: %s", e)

    return report
