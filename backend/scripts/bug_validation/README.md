# Bug-fix validation scripts

One-off scripts that demonstrate the expected behavior of each fix in PR #83
(see `docs/pre-release-v0.0.3-bugs.md`). They are not part of the test suite — run them by hand
when you want a live, end-to-end-ish trace of what the fix does.

Each script:

- Stubs the smallest set of external collaborators (Modal volume, providers,
  loggers).
- Exercises the code path the bug affected.
- Prints **PASS** / **FAIL** based on the post-fix expected behavior.

Run from the `backend/` directory so imports resolve:

```bash
cd backend
.venv/bin/python scripts/bug_validation/exp_write_to_volume_bytes.py
.venv/bin/python scripts/bug_validation/exp_thinking_plumbing.py
.venv/bin/python scripts/bug_validation/exp_validator_await.py
.venv/bin/python scripts/bug_validation/exp_factory_narrow_except.py
.venv/bin/python scripts/bug_validation/exp_step_filename_seeding.py
.venv/bin/python scripts/bug_validation/exp_sandbox_span_exit.py
```

Or all in one go:

```bash
for f in scripts/bug_validation/exp_*.py; do
  echo "===== $f ====="
  .venv/bin/python "$f" || { echo "FAILED: $f"; exit 1; }
done
```

| Script | Validates |
|---|---|
| `exp_write_to_volume_bytes.py` | B1 — `write_to_volume(bytes, ...)` no longer raises `TypeError` |
| `exp_thinking_plumbing.py` | B3 — `thinking_level` flows from runner → `to_provider_config` → provider kwargs |
| `exp_validator_await.py` | B2 — `validate_train_output` returns a usable dict when an `Artifact` row points at the report |
| `exp_factory_narrow_except.py` | A5 — non-ImportError programming errors during provider import propagate (no silent drop) |
| `exp_step_filename_seeding.py` | A12 — `_script_filename` picks up past the highest on-volume `step_NN_*.py` after restart |
| `exp_sandbox_span_exit.py` | A8 — sandbox span context manager exits cleanly on both happy and exception paths |
