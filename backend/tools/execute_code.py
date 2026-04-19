"""execute_code tool — runs Python in an isolated Modal sandbox."""

from __future__ import annotations

import logging
import re

from services.sandbox import run_code
from services.volume import write_to_volume

logger = logging.getLogger(__name__)

# Per-session state (concurrency-safe — each session gets its own counter/set)
_code_counter: dict[str, int] = {}
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


async def detect_new_files(session_id: str, stage: str, publish_fn):
    """Scan the session workspace and emit file_created for any new files.

    `stage` is the producer agent_type, carried on the event so the UI can
    attribute files to a specific agent. It no longer constrains the scan
    to a subfolder — the agent writes anywhere under /sessions/{sid}/.
    """
    from services.volume import listdir_async, reload_volume_async

    workspace = f"/sessions/{session_id}"
    try:
        await reload_volume_async()
        current_files = set()
        for entry in await listdir_async(workspace, recursive=True):
            if entry.type.name == "FILE":
                current_files.add(entry.path)

        known = _known_files.get(session_id, set())
        new_files = current_files - known
        _known_files[session_id] = current_files

        for path in sorted(new_files):
            name = path.split("/")[-1]
            await publish_fn(
                session_id,
                "file_created",
                {"path": path, "name": name, "type": "file", "stage": stage},
            )

        if new_files:
            logger.info(
                "Detected %d new files in session %s", len(new_files), session_id
            )
    except Exception as e:
        logger.warning("File detection error: %s", e)


def create_handler(
    session_id: str,
    stage: str,
    publish_fn,
    gpu: str | None = None,
    **kwargs,
):
    """Factory: create an execute_code handler bound to a session/stage."""

    async def handler(args: dict):
        code = args.get("code", "") if isinstance(args, dict) else str(args)

        await publish_fn(
            session_id,
            "tool_start",
            {"tool": "execute_code", "input": {"code": code[:500]}},
            role="tool",
        )

        # Auto-save code as a .py file under a shared session scripts/ dir.
        filename = _script_filename(code, session_id)
        script_path = f"/sessions/{session_id}/scripts/{filename}"
        try:
            await write_to_volume(code, script_path)
            _known_files.setdefault(session_id, set()).add(script_path)
            await publish_fn(
                session_id,
                "file_created",
                {"path": script_path, "name": filename, "type": "file", "stage": stage},
            )
        except Exception as e:
            logger.error("Failed to save script %s: %s", filename, e)

        try:
            result = await run_code(code, session_id, stage=stage, gpu=gpu)
        except Exception as e:
            error_msg = f"Sandbox error: {e}"
            await publish_fn(
                session_id,
                "tool_end",
                {"tool": "execute_code", "output": error_msg},
                role="tool",
            )
            return {"content": [{"type": "text", "text": error_msg}], "is_error": True}

        output = result["stdout"]
        if result["returncode"] != 0:
            output = (
                f"Exit code {result['returncode']}.\n"
                f"STDOUT:\n{result['stdout']}\nSTDERR:\n{result['stderr']}"
            )
        elif result["stderr"]:
            output += f"\n[stderr]: {result['stderr']}"
        output = output or "(no output)"

        await detect_new_files(session_id, stage, publish_fn)

        await publish_fn(
            session_id,
            "tool_end",
            {"tool": "execute_code", "output": output[:2000]},
            role="tool",
        )

        return {"content": [{"type": "text", "text": output}]}

    return handler
