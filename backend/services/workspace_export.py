"""Streaming zip export of a session or project workspace.

The exporter walks the Modal volume under `/sessions/{sid}` (or the union
of one project's sessions), reads each file via the async volume helpers,
and feeds the bytes into a `zipfile.ZipFile` backed by an in-memory
buffer that we drain after every write — so the response body trickles
out to the client without ever materializing the full archive in RAM or
on disk.

After the workspace files, synthetic entries (`README.md`,
`requirements.txt`, `trainable.py`, `trainable_local.py`) are appended
at the zip root.
For a project export they appear once at the top level, and each
session's files are namespaced under `sessions/{slug}/`.

Size safety
-----------
A per-export uncompressed byte cap (default 2 GB) protects the backend
from a runaway walk. When hit, the walk stops and a trailing
`__truncated.txt` entry lists the omitted paths and total skipped bytes
— the archive itself is still a valid zip the browser will finish
downloading.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import AsyncIterator, Sequence

from services.trainable_sdk import LOCAL_REQUIREMENTS, LOCAL_SHIM, render_readme
from services.volume import (
    iter_volume_file_chunks_async,
    listdir_async,
    reload_volume_async,
    should_ignore_workspace_path,
)

logger = logging.getLogger(__name__)


# Default uncompressed cap for any single export. ~2 GB matches Modal's
# typical session ceiling once `figures/` image grids accumulate; the
# value is overridable per-call so a future opt-in endpoint can raise
# the cap deliberately.
DEFAULT_MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

# 1 MB read buffer is the sweet spot for Modal volume reads — small
# enough that an aggregator pulling tiny files doesn't stall waiting on
# a large one, big enough that the per-call overhead amortizes.
READ_CHUNK_BYTES = 1024 * 1024


def _slug(name: str, fallback: str) -> str:
    """Sanitize a label into a path-safe slug.

    Two sessions with identical labels are disambiguated by the caller
    (it suffixes the short id). This function only strips characters
    that would break the zip's archive-name layout.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-._")
    return cleaned or fallback


async def _iter_files(root: str) -> AsyncIterator[tuple[str, str, int | None]]:
    """Yield file metadata for every file under `root`.

    Skips entries that match `should_ignore_workspace_path` so the zip
    doesn't ship `__pycache__/`, `.DS_Store`, etc.
    """
    root_clean = root.rstrip("/")
    try:
        entries = await listdir_async(root_clean, recursive=True)
    except FileNotFoundError:
        return
    for entry in entries:
        # Modal's listdir returns directories AND files in recursive
        # mode; only zip files. The exact enum varies by SDK version, so
        # check the `.name` attribute instead of comparing enum objects.
        try:
            etype = entry.type.name
        except AttributeError:
            etype = str(entry.type)
        if etype != "FILE":
            continue
        if should_ignore_workspace_path(entry.path):
            continue
        rel = entry.path
        if rel.startswith(root_clean + "/"):
            rel = rel[len(root_clean) + 1 :]
        elif rel == root_clean:
            continue
        rel = rel.lstrip("/")
        if not rel:
            continue
        yield entry.path, rel, getattr(entry, "size", None)


class _StreamingZipBuffer(io.RawIOBase):
    """`io.BytesIO`-shaped sink that ZipFile writes into; drained per chunk."""

    def __init__(self) -> None:
        super().__init__()
        self._buf = bytearray()
        self._pos = 0

    def writable(self) -> bool:  # pragma: no cover — required override
        return True

    def write(self, b) -> int:
        data = bytes(b)
        self._buf.extend(data)
        self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def flush(self) -> None:  # pragma: no cover — interface stub
        return None

    def drain(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out


async def _stream_workspace_zip(
    *,
    scope: str,
    identifier: str,
    sources: Sequence[tuple[str, str]],
    max_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> AsyncIterator[bytes]:
    """Yield zip-body chunks for `sources`.

    `sources` is a list of `(volume_root, archive_prefix)` — session
    exports pass one tuple, project exports pass one per session. Each
    prefix is joined onto every file's archive name, so a project export
    interleaves `sessions/foo/src/a.py`, `sessions/bar/src/a.py`, etc.,
    without collisions.

    The terminal `__truncated.txt` entry is only added if the walk
    actually hit the cap.
    """
    # Refresh once at the top so the export sees writes from a sandbox
    # that finished moments ago. A per-source reload would multiply the
    # latency without much benefit — sessions in the same project rarely
    # diverge by more than a few seconds.
    try:
        await reload_volume_async()
    except Exception as exc:
        logger.debug("workspace_export: volume reload skipped: %s", exc)

    buf = _StreamingZipBuffer()
    zf = zipfile.ZipFile(
        buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    )

    written_bytes = 0
    file_count = 0
    truncated_paths: list[str] = []
    zf_closed = False

    try:
        for volume_root, archive_prefix in sources:
            prefix = archive_prefix.strip("/")
            async for volume_path, rel, file_size in _iter_files(volume_root):
                arcname = f"{prefix}/{rel}" if prefix else rel
                if file_size is None:
                    logger.warning(
                        "workspace_export: skipping %s because size metadata is missing",
                        volume_path,
                    )
                    truncated_paths.append(f"{arcname} (unknown size)")
                    continue

                if written_bytes + file_size > max_bytes:
                    truncated_paths.append(arcname)
                    # Keep walking so the truncated.txt is complete. The
                    # size check happens before opening the file, so an
                    # oversized file is not loaded into backend memory.
                    continue

                try:
                    with zf.open(arcname, "w", force_zip64=True) as dest:
                        async for chunk in iter_volume_file_chunks_async(
                            volume_path, chunk_size=READ_CHUNK_BYTES
                        ):
                            if not chunk:
                                continue
                            dest.write(chunk)
                            out = buf.drain()
                            if out:
                                yield out
                except Exception as exc:
                    logger.warning(
                        "workspace_export: skipping unreadable %s: %s", volume_path, exc
                    )
                    continue

                written_bytes += file_size
                file_count += 1
                chunk = buf.drain()
                if chunk:
                    yield chunk

        # Synthetic entries at the archive root — one set per export, not
        # per session, so a project zip doesn't ship N redundant copies.
        readme = render_readme(
            scope=scope,
            identifier=identifier,
            file_count=file_count,
            total_bytes=written_bytes,
        )
        zf.writestr("README.md", readme)
        zf.writestr("requirements.txt", LOCAL_REQUIREMENTS)
        zf.writestr("trainable.py", LOCAL_SHIM)
        zf.writestr("trainable_local.py", LOCAL_SHIM)

        if truncated_paths:
            truncated_blob = (
                f"# Workspace export hit the {max_bytes:,}-byte cap.\n"
                f"# The following {len(truncated_paths)} file(s) were omitted:\n\n"
                + "\n".join(truncated_paths)
                + "\n"
            )
            zf.writestr("__truncated.txt", truncated_blob)
            logger.warning(
                "workspace_export %s/%s truncated: %d files omitted",
                scope,
                identifier,
                len(truncated_paths),
            )

        chunk = buf.drain()
        if chunk:
            yield chunk

        zf.close()
        zf_closed = True
        chunk = buf.drain()
        if chunk:
            yield chunk

    finally:
        if not zf_closed:
            zf.close()

    logger.info(
        "workspace_export %s/%s done: %d files, %d bytes (%d truncated)",
        scope,
        identifier,
        file_count,
        written_bytes,
        len(truncated_paths),
    )


async def stream_session_zip(
    session_id: str,
    *,
    max_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> AsyncIterator[bytes]:
    """Stream the zip body for one session."""
    sources = [(f"/sessions/{session_id}", "")]
    async for chunk in _stream_workspace_zip(
        scope="session",
        identifier=session_id,
        sources=sources,
        max_bytes=max_bytes,
    ):
        yield chunk


async def stream_project_zip(
    project_id: str,
    sessions: Sequence[tuple[str, str | None]],
    *,
    max_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> AsyncIterator[bytes]:
    """Stream the zip body for a project.

    `sessions` is `[(session_id, optional_label), ...]`. Sessions with
    duplicate labels are disambiguated by short-id suffix.
    """
    if not sessions:
        return

    # Resolve slugs up-front so per-file archive names are stable.
    used: set[str] = set()
    sources: list[tuple[str, str]] = []
    for session_id, label in sessions:
        slug = _slug(label or session_id, session_id[:8])
        if slug in used:
            slug = f"{slug}-{session_id[:8]}"
        used.add(slug)
        sources.append((f"/sessions/{session_id}", f"sessions/{slug}"))

    async for chunk in _stream_workspace_zip(
        scope="project",
        identifier=project_id,
        sources=sources,
        max_bytes=max_bytes,
    ):
        yield chunk
