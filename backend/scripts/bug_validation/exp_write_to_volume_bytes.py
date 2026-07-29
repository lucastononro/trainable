"""B1 — `write_to_volume(bytes, ...)` must accept bytes.

Pre-fix: temp file opened in `mode="w"` (text). Passing `bytes` raised
`TypeError: write() argument must be str, not bytes`. The error was
swallowed by `register_model_declared`'s best-effort copy block, so the
advertised `/projects/{pid}/models/.../v{N}/model.{ext}` registry path
never actually landed.

Post-fix: bytes flow through cleanly, str also still works.

Run:
    cd backend && .venv/bin/python scripts/bug_validation/exp_write_to_volume_bytes.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import MagicMock

# Ensure repo-root imports resolve when running from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def _patch_modal_volume():
    captured: dict = {}
    batch = MagicMock()

    class _Ctx:
        def __enter__(self_inner):
            return batch

        def __exit__(self_inner, *exc):
            return False

    def _put_file(local: str, remote: str):
        with open(local, "rb") as f:
            captured["body"] = f.read()
        captured["remote"] = remote

    batch.put_file.side_effect = _put_file
    fake_vol = MagicMock()
    fake_vol.batch_upload.return_value = _Ctx()
    return fake_vol, captured


async def main() -> int:
    from services import volume

    fake_vol, captured = _patch_modal_volume()
    volume.get_volume = lambda: fake_vol

    # 1. Bytes path — used by register-model artifact copy.
    payload = b"\x00\x01\x02not-text\xff"
    await volume.write_to_volume(payload, "/projects/p/models/m/v1/model.pkl")
    assert captured["body"] == payload, "byte payload corrupted in transit"
    assert captured["remote"] == "/projects/p/models/m/v1/model.pkl"
    print(f"  bytes  -> {captured['remote']} ({len(captured['body'])}B)")

    # 2. Str path — used by ensure_session_workspace.
    captured.clear()
    txt = "print('hello world')\n"
    await volume.write_to_volume(txt, "/sessions/sid/scripts/step_01.py")
    assert captured["body"] == txt.encode("utf-8"), "text payload corrupted"
    print(f"  str    -> {captured['remote']} ({len(captured['body'])}B)")

    print("PASS — write_to_volume accepts both bytes and str")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
