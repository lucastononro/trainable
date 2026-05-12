"""Tests for the streaming-zip workspace exporter and download endpoints."""

from __future__ import annotations

import io
import sys
import zipfile
from contextlib import ExitStack
from pathlib import Path

import pytest

from tests.conftest import MockVolume, mock_volume_patches


def _files_for_session(session_id: str) -> dict[str, bytes]:
    base = f"/sessions/{session_id}"
    return {
        f"{base}/src/__init__.py": b"",
        f"{base}/src/data.py": b"import pandas as pd\nprint('hi')\n",
        f"{base}/src/features.py": b"def build_x(df):\n    return df\n",
        f"{base}/notebooks/01_eda.ipynb": b'{"cells": []}',
        f"{base}/figures/loss/10.png": b"fake-png-bytes",
        f"{base}/figures/loss/20.png": b"more-fake-png-bytes",
        f"{base}/scripts/step_07_train.py": b"# audit script\n",
        # Noise that the exporter must skip.
        f"{base}/__pycache__/data.cpython-311.pyc": b"junk",
        f"{base}/.DS_Store": b"\x00\x00\x00",
    }


async def _drain(agen) -> bytes:
    chunks = []
    async for c in agen:
        chunks.append(c)
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_session_zip_contains_workspace_and_synthetic_files():
    vol = MockVolume(_files_for_session("sess-aaaa"))

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.workspace_export"):
            stack.enter_context(p)

        from services.workspace_export import stream_session_zip

        body = await _drain(stream_session_zip("sess-aaaa"))

    zf = zipfile.ZipFile(io.BytesIO(body))
    names = set(zf.namelist())

    # Workspace files preserved with relative paths (no /sessions/<id>/ prefix).
    assert "src/__init__.py" in names
    assert "src/data.py" in names
    assert "src/features.py" in names
    assert "notebooks/01_eda.ipynb" in names
    assert "figures/loss/10.png" in names
    assert "figures/loss/20.png" in names
    assert "scripts/step_07_train.py" in names

    # Synthetic entries at the zip root.
    assert "README.md" in names
    assert "requirements.txt" in names
    assert "trainable_local.py" in names

    # No noise.
    assert not any("__pycache__" in n for n in names)
    assert not any(n.endswith(".DS_Store") for n in names)

    # Content sanity.
    assert zf.read("src/data.py") == b"import pandas as pd\nprint('hi')\n"
    assert b"trainable" in zf.read("trainable_local.py")
    assert b"pip install -r requirements.txt" in zf.read("README.md")


@pytest.mark.asyncio
async def test_session_zip_is_well_formed_when_workspace_is_empty():
    vol = MockVolume({})

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.workspace_export"):
            stack.enter_context(p)

        from services.workspace_export import stream_session_zip

        body = await _drain(stream_session_zip("empty-sess"))

    zf = zipfile.ZipFile(io.BytesIO(body))
    names = set(zf.namelist())
    # Empty session still ships the three synthetic files (issue acceptance).
    assert names == {"README.md", "requirements.txt", "trainable_local.py"}


@pytest.mark.asyncio
async def test_project_zip_namespaces_each_session():
    files = {}
    files.update(_files_for_session("sess-alpha"))
    files.update(_files_for_session("sess-bravo"))
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.workspace_export"):
            stack.enter_context(p)

        from services.workspace_export import stream_project_zip

        body = await _drain(
            stream_project_zip(
                "proj-x",
                [("sess-alpha", "EDA pass"), ("sess-bravo", "Train pass")],
            )
        )

    zf = zipfile.ZipFile(io.BytesIO(body))
    names = set(zf.namelist())
    assert "sessions/EDA-pass/src/data.py" in names
    assert "sessions/Train-pass/src/data.py" in names
    # Synthetic entries appear exactly once at the project root.
    assert "README.md" in names
    assert "requirements.txt" in names
    assert "trainable_local.py" in names
    assert sum(1 for n in names if n == "README.md") == 1


@pytest.mark.asyncio
async def test_project_zip_disambiguates_duplicate_labels():
    files = {}
    files.update(_files_for_session("sess-aaaa1111"))
    files.update(_files_for_session("sess-bbbb2222"))
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.workspace_export"):
            stack.enter_context(p)

        from services.workspace_export import stream_project_zip

        body = await _drain(
            stream_project_zip(
                "proj-dup",
                [
                    ("sess-aaaa1111", "draft"),
                    ("sess-bbbb2222", "draft"),
                ],
            )
        )

    zf = zipfile.ZipFile(io.BytesIO(body))
    names = set(zf.namelist())
    assert any(n.startswith("sessions/draft/") for n in names)
    # Second "draft" gets a short-id suffix (first 8 chars of session id)
    # so the two sessions don't collide in the archive.
    assert any(n.startswith("sessions/draft-sess-bbb/") for n in names)


@pytest.mark.asyncio
async def test_export_caps_uncompressed_bytes_and_emits_truncated_marker():
    big_payload = b"x" * (50 * 1024)
    files = {f"/sessions/big/data/file_{i:02d}.bin": big_payload for i in range(10)}
    vol = MockVolume(files)

    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.workspace_export"):
            stack.enter_context(p)

        from services.workspace_export import stream_session_zip

        # Cap = 150 KB → only ~3 files fit before truncation kicks in.
        body = await _drain(stream_session_zip("big", max_bytes=150 * 1024))

    zf = zipfile.ZipFile(io.BytesIO(body))
    names = set(zf.namelist())
    assert "__truncated.txt" in names
    truncated_blob = zf.read("__truncated.txt").decode("utf-8")
    assert "153,600-byte cap" in truncated_blob
    # At least one file fit, at least one was skipped.
    data_files = [n for n in names if n.startswith("data/")]
    assert 0 < len(data_files) < 10


def test_local_shim_imports_and_logs(tmp_path: Path):
    """The shipped trainable_local.py must work on a vanilla Python install."""
    from services.trainable_sdk import LOCAL_SHIM

    shim_dir = tmp_path / "pkg"
    shim_dir.mkdir()
    (shim_dir / "trainable_local.py").write_text(LOCAL_SHIM)

    # Point the shim's output dir at the test's tmp path.
    out_dir = tmp_path / "out"
    monkey_env = {"TRAINABLE_LOCAL_OUT": str(out_dir)}

    import os
    import subprocess
    import textwrap

    runner = tmp_path / "runner.py"
    runner.write_text(
        textwrap.dedent(
            """
            import sys
            sys.path.insert(0, %r)
            import trainable_local  # registers ./trainable_out-style module
            import trainable
            trainable.log(1, {"loss": 0.5})
            trainable.log(2, {"loss": 0.25, "acc": 0.9})
            """
        )
        % str(shim_dir)
    )

    env = os.environ.copy()
    env.update(monkey_env)
    result = subprocess.run(
        [sys.executable, str(runner)],
        capture_output=True,
        env=env,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    metrics_file = out_dir / "metrics.jsonl"
    assert metrics_file.exists(), result.stderr
    lines = metrics_file.read_text().strip().splitlines()
    assert len(lines) == 2
    assert '"loss": 0.5' in lines[0]
    assert '"acc": 0.9' in lines[1]


@pytest.mark.asyncio
async def test_session_download_endpoint_returns_zip(client, default_project_id):
    # Insert a session via the ORM directly — avoids depending on the
    # exact shape of the session-create REST endpoint, which is tested
    # elsewhere.
    import uuid

    from db import async_session
    from models import Session as SessionModel

    sid = str(uuid.uuid4())
    async with async_session() as db:
        db.add(SessionModel(id=sid, project_id=default_project_id))
        await db.commit()

    vol = MockVolume(_files_for_session(sid))
    with ExitStack() as stack:
        for p in mock_volume_patches(vol, "services.workspace_export"):
            stack.enter_context(p)
        resp = await client.get(f"/api/sessions/{sid}/download")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"].startswith("attachment; filename=")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert "src/data.py" in names
    assert "trainable_local.py" in names


@pytest.mark.asyncio
async def test_session_download_404_when_session_missing(client):
    resp = await client.get("/api/sessions/does-not-exist/download")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_project_download_404_when_no_sessions(client, default_project_id):
    resp = await client.get(f"/api/projects/{default_project_id}/download")
    assert resp.status_code == 404
