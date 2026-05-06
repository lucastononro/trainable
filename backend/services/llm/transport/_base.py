"""Subprocess transport primitives for OAuth-CLI providers.

Codex CLI and Gemini CLI both speak structured JSON-Lines. We share one helper
that spawns the child process, pumps its stdout line-by-line, and yields parsed
events. Each transport (codex_cli.py, gemini_cli.py) wraps this with a
provider-specific argv builder and event translator.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class TransportError(RuntimeError):
    """Raised when a subprocess transport fails to start, hangs, or exits non-zero."""


async def spawn_jsonl(
    cmd: list[str],
    *,
    stdin_payload: str | None = None,
    env: dict | None = None,
    cwd: str | None = None,
    timeout_seconds: int = 1800,
) -> AsyncIterator[dict]:
    """Spawn a child process and yield JSON dicts parsed from its stdout, line by line.

    The child is expected to emit one JSON object per line on stdout. Anything
    that doesn't parse is logged and skipped — we don't want a single bad line
    to abort the stream. stderr is drained into the logger at debug level.

    Raises TransportError if the process can't start. The async generator
    cleans up the child on cancellation / completion.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_payload is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        raise TransportError(f"CLI not found on PATH: {cmd[0]}") from e
    except OSError as e:
        raise TransportError(f"Failed to spawn {cmd[0]}: {e}") from e

    # Push the payload into stdin and close it so the child sees EOF.
    if stdin_payload is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_payload.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception as e:
            logger.warning("Failed to send stdin to %s: %s", cmd[0], e)

    async def _drain_stderr():
        if proc.stderr is None:
            return
        async for line in proc.stderr:
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.debug("[%s stderr] %s", cmd[0], text)

    stderr_task = asyncio.create_task(_drain_stderr())
    assert proc.stdout is not None

    try:
        async with asyncio.timeout(timeout_seconds):
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("[%s stdout non-JSON] %s", cmd[0], line[:200])
                    continue
    finally:
        stderr_task.cancel()
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        rc = proc.returncode
        if rc not in (None, 0):
            logger.warning("%s exited with code %s", cmd[0], rc)
