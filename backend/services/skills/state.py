"""Per-session state and shared helpers for skill handlers.

Lives here (rather than inside a skill's handler.py) because skill handlers
are loaded dynamically via importlib and don't expose a stable import path.
"""

from __future__ import annotations

import re

# execute-code: monotonically-increasing per-session counter used to name
# the script files written to /sessions/{sid}/scripts/.
_code_counter: dict[str, int] = {}

# execute-code: set of file paths the agent has already announced as
# created, so the post-run scanner doesn't re-emit the same file_created
# events on every step.
_known_files: dict[str, set[str]] = {}


def _extract_slug(code: str) -> str:
    """Extract a short descriptive slug from code for naming the script file."""
    for line in code.splitlines():
        line = line.strip()
        if line.startswith("#") and not line.startswith("#!"):
            text = line.lstrip("# ").strip()
            if len(text) > 3:
                slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
                return slug[:40]
        m = re.match(r"(?:def|class)\s+(\w+)", line)
        if m:
            return m.group(1)[:40]
    for line in code.splitlines():
        line = line.strip()
        if line.startswith("import ") or line.startswith("from "):
            mod = line.split()[-1].split(".")[-1]
            return mod[:40]
    return "code"


def _script_filename(code: str, session_id: str) -> str:
    """Generate a sequential, descriptive filename for a code execution."""
    counter = _code_counter.get(session_id, 0) + 1
    _code_counter[session_id] = counter
    slug = _extract_slug(code)
    return f"step_{counter:02d}_{slug}.py"


def cleanup_session(session_id: str) -> None:
    """Drop per-session state when an agent run completes."""
    _known_files.pop(session_id, None)
    _code_counter.pop(session_id, None)
