"""execute-code skill — runs Python in an isolated Modal sandbox."""

from __future__ import annotations

import logging

from services.sandbox import run_code
from services.skills.state import (
    _code_counter,
    _extract_slug,
    _known_files,
    _script_filename,
)
from services.volume import write_to_volume

logger = logging.getLogger(__name__)


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
    sandbox_config: dict | None = None,
    parent_agent_type: str | None = None,
    parent_agent_id: str | None = None,
    **kwargs,
):
    """Factory: create an execute_code handler bound to a session/stage."""

    _sandbox_config = sandbox_config or {}
    _agent_type = parent_agent_type or stage
    _agent_id = parent_agent_id or "root"

    async def handler(args: dict):
        code = args.get("code", "") if isinstance(args, dict) else str(args)
        heavy = args.get("heavy", False) if isinstance(args, dict) else False

        # Pick the right sandbox profile based on heavy flag
        profile_key = "training" if heavy else "default"
        profile = _sandbox_config.get(profile_key) or {}
        gpu = profile.get("gpu")
        timeout = profile.get("timeout")

        await publish_fn(
            session_id,
            "tool_start",
            {"tool": "execute_code", "input": {"code": code[:500], "heavy": heavy}},
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
            result = await run_code(
                code,
                session_id,
                stage=stage,
                gpu=gpu,
                timeout=timeout,
                agent_type=_agent_type,
                agent_id=_agent_id,
            )
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
