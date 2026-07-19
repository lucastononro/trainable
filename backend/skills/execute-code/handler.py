"""execute-code skill — runs Python in an isolated sandbox on the
configured compute provider."""

from __future__ import annotations

import logging
import time

import modal.exception as modal_exc

from services.compute.base import SandboxTimeoutError
from services.compute_allowance import (
    clamp_timeout,
    normalize_gpu,
    resolve_compute_allowance,
)
from services.sandbox import run_code
from services.skills.state import (
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
        requested_gpu = args.get("gpu") if isinstance(args, dict) else None
        requested_timeout = args.get("timeout") if isinstance(args, dict) else None

        # Compute selection precedence: explicit `gpu` arg > heavy profile
        # > default profile. The explicit path is validated against the
        # project's compute allowance (see services/compute_allowance.py —
        # the same resolver renders the allowance into the system prompt).
        allowance = resolve_compute_allowance(_sandbox_config)
        profile_key = "training" if heavy else "default"
        profile = _sandbox_config.get(profile_key) or {}
        gpu = profile.get("gpu")
        timeout = profile.get("timeout")

        start = time.time()

        if requested_gpu:
            label = normalize_gpu(requested_gpu)
            if label is None or not allowance.permits(label):
                error_msg = (
                    f"GPU '{requested_gpu}' is not available in this project. "
                    f"Allowed: {', '.join(allowance.allowed_gpus)}. "
                    f"Re-call execute-code with one of those values, or omit "
                    f"`gpu` to use the {profile_key} profile."
                )
                await publish_fn(
                    session_id,
                    "tool_end",
                    {"tool": "execute_code", "output": error_msg, "duration": 0},
                    role="tool",
                )
                return {
                    "content": [{"type": "text", "text": error_msg}],
                    "is_error": True,
                }
            # "cpu" is an explicit request for no GPU; run_code(gpu=None)
            # schedules on the CPU pool.
            gpu = None if label == "cpu" else label

        if requested_timeout is not None:
            clamped = clamp_timeout(requested_timeout, allowance)
            if clamped is not None:
                timeout = clamped

        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "execute_code",
                "input": {"code": code[:500], "heavy": heavy},
                "gpu": gpu or "cpu",
                "timeout": timeout,
            },
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
        except (
            modal_exc.SandboxTimeoutError,
            modal_exc.TimeoutError,
            SandboxTimeoutError,
        ) as e:
            # The provider killed the sandbox at its configured timeout. Surface this
            # as a *tool_output* with is_error=True (not a session crash) so
            # the model recognises the timeout and can decide: stop, retry
            # with a smaller chunk, escalate to a heavier profile, or pivot.
            elapsed = round(time.time() - start, 1)
            error_msg = (
                f"Sandbox timed out after {elapsed}s "
                f"(profile={profile_key}, configured timeout={timeout or 'default'}s). "
                f"The Python process was killed mid-execution and partial "
                f"output (if any) is lost. Options: (a) split the work into "
                f"smaller chunks, (b) reduce data size or iterations, "
                f"(c) re-run with heavy=true for the GPU/training profile, "
                f"(d) re-run with an explicit `timeout=` up to "
                f"{allowance.max_timeout}s. "
                f"Underlying error: {e.__class__.__name__}"
            )
            logger.warning("Sandbox timeout (session=%s): %s", session_id, e)
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "execute_code",
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
                    "tool": "execute_code",
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
                "tool": "execute_code",
                "output": output[:2000],
                "duration": round(time.time() - start, 1),
            },
            role="tool",
        )

        return {"content": [{"type": "text", "text": output}]}

    return handler
