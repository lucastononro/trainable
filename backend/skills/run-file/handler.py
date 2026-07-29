"""run-file skill — execute an existing workspace file in the sandbox.

Runs the file via `runpy.run_path(path, run_name="__main__")` through
services.sandbox.run_code, so it gets the same preamble as execute-code
(`src/` on sys.path, session workspace as cwd). The audit log gets a
one-line `runpy.run_path(...)` script — it records WHAT ran; the body
already lives on disk.
"""

from __future__ import annotations

import logging
import time

import modal.exception as modal_exc

from config import settings
from services.compute.base import SandboxTimeoutError
from services.compute_allowance import resolve_compute_allowance
from services.sandbox import run_code
from services.skills.state import (
    _known_files,
    _script_filename,
    resolve_session_path,
)
from services.volume import read_volume_file_async, write_to_volume

logger = logging.getLogger(__name__)


async def detect_new_files(session_id: str, stage: str, publish_fn):
    """Scan the session workspace and emit file_created for any new files.

    Same contract as the execute-code post-run scan.
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
    stage: str = None,
    publish_fn=None,
    sandbox_config: dict | None = None,
    parent_agent_type: str | None = None,
    parent_agent_id: str | None = None,
    **kwargs,
):
    """Factory: create a run_file handler bound to a session/stage."""

    _sandbox_config = sandbox_config or {}
    _agent_type = parent_agent_type or stage
    _agent_id = parent_agent_id or "root"

    async def handler(args: dict):
        raw_path = args.get("path") if isinstance(args, dict) else None
        heavy = args.get("heavy", False) if isinstance(args, dict) else False

        try:
            path, rel_path = resolve_session_path(session_id, raw_path)
        except ValueError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}

        # Compute selection: the heavy flag picks the training profile,
        # same as execute-code. An owner-set max_timeout caps every run.
        allowance = resolve_compute_allowance(_sandbox_config)
        profile_key = "training" if heavy else "default"
        profile = _sandbox_config.get(profile_key) or {}
        gpu = profile.get("gpu")
        timeout = min(
            profile.get("timeout") or settings.sandbox_timeout, allowance.max_timeout
        )

        start = time.time()

        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "run_file",
                "input": {"path": rel_path, "heavy": heavy},
                "gpu": gpu or "cpu",
                "timeout": timeout,
            },
            role="tool",
        )

        try:
            await read_volume_file_async(path)
        except Exception:
            msg = (
                f"File not found: {path}. Author it with write-file first, "
                "then run-file it."
            )
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "run_file",
                    "output": msg,
                    "duration": round(time.time() - start, 1),
                },
                role="tool",
            )
            return {"content": [{"type": "text", "text": msg}], "is_error": True}

        # The sandbox cwd is the session workspace, so the relative path is
        # what runpy sees. The leading comment makes the auto-saved audit
        # script self-describing (scripts/step_NN_run_<name>.py).
        code = (
            f"# run {rel_path}\n"
            "import runpy\n\n"
            f"runpy.run_path({rel_path!r}, run_name='__main__')\n"
        )

        # Audit log: one-line "we ran X" entry — the body stays on disk.
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
        except (
            modal_exc.SandboxTimeoutError,
            modal_exc.TimeoutError,
            SandboxTimeoutError,
        ) as e:
            elapsed = round(time.time() - start, 1)
            error_msg = (
                f"Sandbox timed out after {elapsed}s running {rel_path} "
                f"(profile={profile_key}, configured timeout={timeout}s). "
                "The Python process was killed mid-execution and partial "
                "output (if any) is lost. Options: (a) split the work into "
                "smaller files/functions, (b) reduce data size or iterations, "
                "(c) re-run with heavy=true for the GPU/training profile. "
                f"Underlying error: {e.__class__.__name__}"
            )
            logger.warning("Sandbox timeout (session=%s): %s", session_id, e)
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "run_file",
                    "output": error_msg,
                    "duration": elapsed,
                    "timed_out": True,
                },
                role="tool",
            )
            return {"content": [{"type": "text", "text": error_msg}], "is_error": True}
        except Exception as e:
            error_msg = f"Sandbox error: {e}"
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "run_file",
                    "output": error_msg,
                    "duration": round(time.time() - start, 1),
                },
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
            {
                "tool": "run_file",
                "output": output[:2000],
                "duration": round(time.time() - start, 1),
            },
            role="tool",
        )

        return {"content": [{"type": "text", "text": output}]}

    return handler
