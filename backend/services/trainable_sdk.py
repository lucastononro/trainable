"""Synthetic files shipped inside a workspace-export zip."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import resources


def _read_local_shim() -> str:
    """Return the Trainable runtime source packaged into exports."""
    return (
        resources.files("services")
        .joinpath("trainable_runtime.py")
        .read_text(encoding="utf-8")
    )


def _read_local_requirements() -> str:
    """Return the requirements.txt content packaged into exports."""
    return (
        resources.files("services")
        .joinpath("workspace_export_requirements.txt")
        .read_text(encoding="utf-8")
    )


# The exporter writes this source as both `trainable.py` and `trainable_local.py`.
# Keeping it in a normal module makes the shim lintable and avoids maintaining
# hundreds of lines of Python inside a triple-quoted string.
LOCAL_SHIM = _read_local_shim()


# Subset of `backend/requirements.txt` useful for running downloaded scripts.
# Kept as a real requirements file so updates are diffable and tool-friendly.
LOCAL_REQUIREMENTS = _read_local_requirements()


def render_readme(
    *, scope: str, identifier: str, file_count: int, total_bytes: int
) -> str:
    """Generate the README packaged at the zip root.

    `scope` is either "session" or "project" - affects the top-level
    description but the run instructions are identical.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    short = identifier[:8] if len(identifier) > 8 else identifier
    title = f"Trainable {scope} export - {short}"
    size_mb = total_bytes / (1024 * 1024)

    if scope == "project":
        layout_note = (
            "Each session is nested under `sessions/<slug>/`. Two sessions\n"
            "with identical labels are disambiguated by short-id suffix.\n"
        )
    else:
        layout_note = (
            "Files preserve the layout the agent wrote in the cloud, so\n"
            "imports between `src/` modules keep working unchanged.\n"
        )

    return f"""# {title}

Generated on {ts} from {scope} `{identifier}` ({file_count} files, {size_mb:.1f} MB).

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
export PYTHONPATH="$PWD:${{PYTHONPATH:-}}"
python -c "from trainable import log; log(1, {{'loss': 0.5}})"
```

Any of the agent's scripts that do `from trainable import log,
log_image, ...` will work against your filesystem as long as this export
root is on `PYTHONPATH`.
For notebooks or custom entrypoints that prefer an explicit setup step,
you can still import the companion shim:

```python
import trainable_local  # registers ./trainable_out/-backed `trainable` module
```

...or set `PYTHONSTARTUP=trainable_local.py` for a session-wide shim.

## What's inside

- `src/`             - agent-written Python modules (the session was a
                        proper Python package: `from features import ...`
                        works the same here).
- `notebooks/`       - Jupyter notebooks as-is.
- `figures/`         - image artifacts the agent logged.
- `scripts/`         - the audit trail of every sandbox call.
- `trainable.py`       - local shim for the `trainable` SDK.
- `trainable_local.py` - explicit-import alias for the same shim.
- `requirements.txt` - pinned subset of the cloud sandbox image.

{layout_note}

## What's NOT inside

- **Raw datasets.** The agent's scripts reference inputs by their original
  upload path (e.g. `/sessions/.../data/raw.csv`). Drop your local copy
  in place and adjust the path, or set the `DATA_DIR` env var if a script
  reads it.
- **The GPU/Modal environment.** Library versions are best-effort, not
  byte-identical. Scripts that pin specific GPU kernels or Modal-only
  paths may need light edits.
- **Round-trip telemetry.** `trainable.log(...)` writes to
  `./trainable_out/metrics.jsonl` here - nothing is sent back to the
  studio.
"""
