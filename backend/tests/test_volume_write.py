"""Regression: `write_to_volume` must accept bytes.

Every model-promotion caller passes bytes from `read_volume_file_async`.
The original `mode="w"` text-only path silently failed for years (the
caller's best-effort except block swallowed the TypeError and pinned the
registered model's artifact_uri to the agent-supplied path instead of the
stable `/projects/{pid}/models/.../v{N}/model.{ext}` copy).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def _patched_volume(monkeypatch, captured: dict):
    """Stub `get_volume()` so `batch_upload` records what we'd send."""
    fake_vol = MagicMock()
    batch = MagicMock()

    class _Ctx:
        def __enter__(self_inner):
            return batch

        def __exit__(self_inner, *exc):
            return False

    def _put_file(local, remote):
        with open(local, "rb") as f:
            captured["body"] = f.read()
        captured["remote"] = remote

    batch.put_file.side_effect = _put_file
    fake_vol.batch_upload.return_value = _Ctx()

    from services import volume as vol_mod

    monkeypatch.setattr(vol_mod, "get_volume", lambda: fake_vol)
    return vol_mod


def test_write_to_volume_accepts_bytes(monkeypatch):
    captured: dict = {}
    vol_mod = _patched_volume(monkeypatch, captured)

    payload = b"\x00\x01\x02\x03not-text\xff"
    asyncio.run(vol_mod.write_to_volume(payload, "/projects/p/models/m/v1/model.pkl"))

    assert captured["body"] == payload
    assert captured["remote"] == "/projects/p/models/m/v1/model.pkl"


def test_write_to_volume_accepts_str(monkeypatch):
    captured: dict = {}
    vol_mod = _patched_volume(monkeypatch, captured)

    payload = "print('hello world')\n"
    asyncio.run(vol_mod.write_to_volume(payload, "/sessions/sid/scripts/step_01.py"))

    assert captured["body"] == payload.encode("utf-8")
    assert captured["remote"] == "/sessions/sid/scripts/step_01.py"
