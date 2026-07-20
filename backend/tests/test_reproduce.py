"""Tests for the snapshot reproduce action (services/reproduce.py).

The sandbox call is faked throughout — reproduction logic (script
selection, input verification, metric parsing, drift diffing, report
shape) is exercised against an in-memory DB + mocked volume.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from db import async_session
from models import Metric, RunSnapshot, Session
from services.reproduce import (
    build_replay_code,
    diff_metrics,
    final_metric_values,
    select_replay_scripts,
    verify_inputs,
)

SID = "repro-session"
WORKSPACE = f"/sessions/{SID}"


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


def test_diff_metrics_match_within_tolerance():
    out = diff_metrics({"acc": 0.91}, {"acc": 0.91 + 1e-9})
    assert out["drift_detected"] is False
    assert out["rows"][0]["status"] == "match"
    assert out["summary"] == {"matched": 1, "drifted": 0, "missing": 0, "new": 0}


def test_diff_metrics_flags_drift():
    out = diff_metrics({"acc": 0.91, "loss": 0.30}, {"acc": 0.85, "loss": 0.30})
    assert out["drift_detected"] is True
    by_name = {r["name"]: r for r in out["rows"]}
    assert by_name["acc"]["status"] == "drift"
    assert by_name["acc"]["abs_diff"] == pytest.approx(0.06)
    assert by_name["acc"]["rel_diff"] == pytest.approx(0.06 / 0.91)
    assert by_name["loss"]["status"] == "match"
    assert out["summary"]["drifted"] == 1


def test_diff_metrics_missing_counts_as_drift():
    out = diff_metrics({"acc": 0.91}, {})
    assert out["drift_detected"] is True
    assert out["rows"][0]["status"] == "missing"
    assert out["rows"][0]["reproduced"] is None


def test_diff_metrics_new_metric_is_informational():
    out = diff_metrics({}, {"f1": 0.8})
    assert out["drift_detected"] is False
    assert out["rows"][0]["status"] == "new"


def test_diff_metrics_custom_tolerance():
    strict = diff_metrics({"acc": 0.90}, {"acc": 0.905}, tolerance=1e-6)
    loose = diff_metrics({"acc": 0.90}, {"acc": 0.905}, tolerance=0.01)
    assert strict["drift_detected"] is True
    assert loose["drift_detected"] is False


def test_final_metric_values_last_step_wins():
    items = [
        {"step": 1, "name": "loss", "value": 0.9},
        {"step": 2, "name": "loss", "value": 0.5},
        {"step": 2, "name": "acc", "value": 0.8},
        {"step": 1, "name": "acc", "value": 0.6},  # lower step, arrives later
    ]
    assert final_metric_values(items) == {"loss": 0.5, "acc": 0.8}


def test_select_replay_scripts_skips_notebooks_and_init():
    manifest = {
        "code": {
            "files": [
                {"path": f"{WORKSPACE}/eda.ipynb", "sha256": "x"},
                {"path": f"{WORKSPACE}/src/__init__.py", "sha256": "x"},
                {"path": f"{WORKSPACE}/src/train.py", "sha256": "x"},
            ]
        }
    }
    assert select_replay_scripts(manifest) == [f"{WORKSPACE}/src/train.py"]


def test_build_replay_code_targets_volume_mount():
    code = build_replay_code([f"{WORKSPACE}/src/train.py"])
    assert f"/data{WORKSPACE}/src/train.py" in code
    assert "run_path" in code
    compile(code, "<replay>", "exec")  # must be valid python


def _runner_for(tmp_path, scripts: dict[str, str]) -> str:
    """Materialize scripts in tmp_path and return the replay runner with the
    sandbox's /data prefix rewritten to point at them (regression seam for
    executing the generated runner outside a sandbox)."""
    paths = []
    for name, body in scripts.items():
        p = tmp_path / name
        p.write_text(body)
        paths.append(str(p))
    return build_replay_code(paths).replace(f"/data{tmp_path}", str(tmp_path))


def test_replay_runner_survives_clean_sys_exit(tmp_path, capsys):
    """Regression: a script ending in sys.exit(0) must not silently abort
    the remaining scripts (SystemExit escapes runpy.run_path)."""
    code = _runner_for(
        tmp_path,
        {
            "a_first.py": "import sys\nprint('metric-from-first')\nsys.exit(0)\n",
            "b_second.py": "print('metric-from-second')\n",
        },
    )
    exec(code, {})  # must not raise SystemExit
    out = capsys.readouterr().out
    assert "metric-from-first" in out
    assert "metric-from-second" in out


def test_replay_runner_bare_sys_exit_is_clean(tmp_path, capsys):
    code = _runner_for(
        tmp_path,
        {
            "a_first.py": "import sys\nsys.exit()\n",  # SystemExit(None)
            "b_second.py": "print('still-ran')\n",
        },
    )
    exec(code, {})
    assert "still-ran" in capsys.readouterr().out


def test_replay_runner_nonzero_sys_exit_aborts(tmp_path, capsys):
    """A genuinely failing script must still fail the whole replay with its
    nonzero code, and later scripts must not run."""
    code = _runner_for(
        tmp_path,
        {
            "a_first.py": "import sys\nsys.exit(3)\n",
            "b_second.py": "print('must-not-run')\n",
        },
    )
    with pytest.raises(SystemExit) as excinfo:
        exec(code, {})
    assert excinfo.value.code == 3
    captured = capsys.readouterr()
    assert "must-not-run" not in captured.out
    assert "exited nonzero" in captured.err


def test_verify_inputs_detects_changed_and_missing_files():
    manifest = {
        "dataset": {
            "files": [{"path": f"{WORKSPACE}/data/train.parquet", "sha256": "aaa"}]
        },
        "code": {
            "files": [
                {"path": f"{WORKSPACE}/src/train.py", "sha256": "bbb"},
                {"path": f"{WORKSPACE}/src/gone.py", "sha256": "ccc"},
            ]
        },
    }
    current_data = [{"path": f"{WORKSPACE}/data/train.parquet", "sha256": "aaa"}]
    current_code = [{"path": f"{WORKSPACE}/src/train.py", "sha256": "MUTATED"}]
    out = verify_inputs(manifest, current_data, current_code)
    assert out["dataset_verified"] is True
    assert out["code_verified"] is False
    changed = {c["path"]: c for c in out["changed_files"]}
    assert changed[f"{WORKSPACE}/src/train.py"]["actual_sha256"] == "MUTATED"
    assert changed[f"{WORKSPACE}/src/gone.py"]["actual_sha256"] is None


def test_verify_inputs_detects_added_files():
    """A file added after the snapshot (e.g. a new module the scripts could
    import) must flip the section's verified flag."""
    manifest = {
        "dataset": {
            "files": [{"path": f"{WORKSPACE}/data/train.parquet", "sha256": "aaa"}]
        },
        "code": {"files": [{"path": f"{WORKSPACE}/src/train.py", "sha256": "bbb"}]},
    }
    current_data = [{"path": f"{WORKSPACE}/data/train.parquet", "sha256": "aaa"}]
    current_code = [
        {"path": f"{WORKSPACE}/src/train.py", "sha256": "bbb"},
        {"path": f"{WORKSPACE}/src/sneaky_new_helper.py", "sha256": "ddd"},
    ]
    out = verify_inputs(manifest, current_data, current_code)
    assert out["dataset_verified"] is True
    assert out["code_verified"] is False
    (added,) = out["changed_files"]
    assert added == {
        "path": f"{WORKSPACE}/src/sneaky_new_helper.py",
        "expected_sha256": None,
        "actual_sha256": "ddd",
    }


# ---------------------------------------------------------------------------
# End-to-end route with faked snapshot + sandbox result
# ---------------------------------------------------------------------------

MANIFEST = {
    "session_id": SID,
    "dataset": {
        "hash": "dhash",
        "files": [{"path": f"{WORKSPACE}/data/train.parquet", "sha256": "aaa"}],
    },
    "code": {
        "hash": "chash",
        "files": [{"path": f"{WORKSPACE}/src/train.py", "sha256": "bbb"}],
    },
    "schema_version": 1,
}


async def _seed_snapshot_and_metrics():
    async with async_session() as db:
        db.add(Session(id=SID, name="repro"))
        # Flush the session row first — RunSnapshot has no `session`
        # relationship, so the unit-of-work can't order the FK inserts.
        await db.commit()
        db.add(
            RunSnapshot(
                session_id=SID,
                dataset_hash="dhash",
                code_hash="chash",
                hyperparams={},
                manifest_uri=f"{WORKSPACE}/snapshot.json",
            )
        )
        # Original run: acc improves over steps; final values are the baseline.
        db.add_all(
            [
                Metric(session_id=SID, stage="train", step=1, name="acc", value=0.70),
                Metric(session_id=SID, stage="train", step=2, name="acc", value=0.91),
                Metric(session_id=SID, stage="train", step=2, name="loss", value=0.30),
            ]
        )
        await db.commit()


def _reproduce_patches(stdout: str, returncode: int = 0):
    """Patch volume + sandbox seams in services.reproduce."""
    import json

    run_code = AsyncMock(
        return_value={"stdout": stdout, "stderr": "", "returncode": returncode}
    )
    current_files = {
        (".parquet", ".csv", ".feather"): [
            {"path": f"{WORKSPACE}/data/train.parquet", "size": 3, "sha256": "aaa"}
        ],
        (".py", ".ipynb"): [
            {"path": f"{WORKSPACE}/src/train.py", "size": 3, "sha256": "bbb"}
        ],
    }

    async def _collect(workspace, suffixes):
        return current_files[suffixes]

    return run_code, [
        patch("services.reproduce.reload_volume_async", new_callable=AsyncMock),
        patch("services.reproduce.write_to_volume", new_callable=AsyncMock),
        patch(
            "services.reproduce.read_volume_file_async",
            new_callable=AsyncMock,
            return_value=json.dumps(MANIFEST).encode(),
        ),
        patch("services.reproduce._collect_files", side_effect=_collect),
        patch("services.reproduce.run_code", run_code),
    ]


@pytest.mark.asyncio
async def test_reproduce_route_detects_drift(client):
    await _seed_snapshot_and_metrics()
    # Replay reproduces loss exactly but lands acc at 0.85 → drift.
    stdout = (
        '{"step": 2, "metrics": {"acc": 0.85, "loss": 0.30}}\nsome non-json noise\n'
    )
    run_code, patches = _reproduce_patches(stdout)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        resp = await client.post(f"/api/sessions/{SID}/snapshot/reproduce")

    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["status"] == "drift"
    assert report["metrics"]["drift_detected"] is True
    by_name = {r["name"]: r for r in report["metrics"]["rows"]}
    assert by_name["acc"]["status"] == "drift"
    assert by_name["acc"]["original"] == pytest.approx(0.91)
    assert by_name["acc"]["reproduced"] == pytest.approx(0.85)
    assert by_name["loss"]["status"] == "match"
    assert report["inputs"]["dataset_verified"] is True
    assert report["inputs"]["code_verified"] is True
    assert report["execution"]["scripts"] == [f"{WORKSPACE}/src/train.py"]

    # The replay must not contaminate the session's recorded metrics:
    # stage=None keeps the sandbox pipeline from persisting metric lines.
    assert run_code.await_count == 1
    assert run_code.await_args.kwargs["stage"] is None


@pytest.mark.asyncio
async def test_reproduce_route_reports_match(client):
    await _seed_snapshot_and_metrics()
    stdout = '{"step": 2, "metrics": {"acc": 0.91, "loss": 0.30}}\n'
    _, patches = _reproduce_patches(stdout)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        resp = await client.post(f"/api/sessions/{SID}/snapshot/reproduce")

    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["status"] == "match"
    assert report["metrics"]["drift_detected"] is False
    assert report["metrics"]["summary"] == {
        "matched": 2,
        "drifted": 0,
        "missing": 0,
        "new": 0,
    }


@pytest.mark.asyncio
async def test_reproduce_route_flags_failed_replay(client):
    await _seed_snapshot_and_metrics()
    _, patches = _reproduce_patches(stdout="", returncode=1)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        resp = await client.post(f"/api/sessions/{SID}/snapshot/reproduce")

    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["status"] == "error"
    assert report["execution"]["returncode"] == 1
    # Nothing reproduced → both original metrics reported missing.
    assert report["metrics"]["summary"]["missing"] == 2


@pytest.mark.asyncio
async def test_reproduce_route_404_without_snapshot(client):
    resp = await client.post("/api/sessions/nope/snapshot/reproduce")
    assert resp.status_code == 404
