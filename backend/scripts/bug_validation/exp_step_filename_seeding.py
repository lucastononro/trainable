"""A12 — `_script_filename` must seed past existing `step_NN_*.py`.

Pre-fix: `_code_counter` lived only in-process. A backend restart
mid-session reset the counter to 0 → `step_01_*.py` got overwritten.

Post-fix: on first call per session, the counter seeds from the highest
on-volume `step_NN_*.py` and then increments in-memory for the rest of
the session.

Run:
    cd backend && .venv/bin/python scripts/bug_validation/exp_step_filename_seeding.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class _Entry:
    def __init__(self, path: str):
        self.path = path


class _FakeVolume:
    def __init__(self, entries):
        self.entries = entries
        self.probes = 0

    def listdir(self, path):
        self.probes += 1
        return iter(self.entries)


def main() -> int:
    from services.skills import state
    from services import volume as volume_mod

    state._code_counter.clear()

    fake_vol = _FakeVolume(
        [
            _Entry("/sessions/abc/scripts/step_01_load.py"),
            _Entry("/sessions/abc/scripts/step_05_train.py"),
            _Entry("/sessions/abc/scripts/step_07_eval.py"),
            _Entry("/sessions/abc/scripts/notes.md"),
        ]
    )
    volume_mod.get_volume = lambda: fake_vol

    # First call after "restart": expect step_08_*.
    name1 = state._script_filename("print('hello')", "abc")
    print(f"  first call  -> {name1}")
    assert name1.startswith("step_08_"), name1

    # Second, third calls: counter increments in-process, no extra Modal probe.
    name2 = state._script_filename("# next step", "abc")
    name3 = state._script_filename("# another", "abc")
    print(f"  second call -> {name2}")
    print(f"  third call  -> {name3}")
    assert name2.startswith("step_09_"), name2
    assert name3.startswith("step_10_"), name3
    assert fake_vol.probes == 1, f"expected 1 volume listdir, got {fake_vol.probes}"

    # Fresh session ID with empty volume: starts at 01.
    fake_vol_empty = _FakeVolume([])
    volume_mod.get_volume = lambda: fake_vol_empty
    name_fresh = state._script_filename("# fresh", "different-session")
    print(f"  fresh sid   -> {name_fresh}")
    assert name_fresh.startswith("step_01_"), name_fresh

    print("PASS — counter seeds from on-volume max, then increments in-process")
    return 0


if __name__ == "__main__":
    sys.exit(main())
