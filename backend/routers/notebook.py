"""Interactive-notebook routes — many named notebooks per session.

Notebooks live under `/sessions/{id}/notebooks/{name}.ipynb` on the Modal
Volume. One persistent Jupyter kernel per session runs every cell; sibling
notebooks share kernel state, which is what lets an agent split an analysis
into multiple themed notebooks (e.g. "data-overview" + "baseline-model")
without losing imported DataFrames or fit models between them.

Cell outputs stream to the frontend via the existing SSE broadcaster with
a `notebook_name` in every payload so a multi-notebook UI can route events.
"""

from __future__ import annotations

import asyncio
import logging

import nbformat
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Artifact
from models import Session as SessionModel
from services import notebook_store
from services.kernel_manager import kernel_manager

logger = logging.getLogger(__name__)
router = APIRouter()


async def _require_session(session_id: str, db: AsyncSession) -> SessionModel:
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


async def _ensure_artifact_row(session_id: str, name: str, db: AsyncSession) -> None:
    path = notebook_store.notebook_path(session_id, name)
    # Use .first() — prior runs occasionally leave duplicate rows for the
    # same path (e.g. from earlier code paths that didn't scope on path).
    # `scalar_one_or_none()` raises MultipleResultsFound in that case.
    result = await db.execute(
        select(Artifact).where(
            Artifact.session_id == session_id,
            Artifact.artifact_type == "notebook",
            Artifact.path == path,
        )
    )
    if result.scalars().first():
        return
    db.add(
        Artifact(
            session_id=session_id,
            stage="notebook",
            artifact_type="notebook",
            name=f"{name}.ipynb",
            path=path,
        )
    )
    await db.commit()


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------


@router.get("/sessions/{session_id}/notebooks")
async def list_notebooks(session_id: str, db: AsyncSession = Depends(get_db)):
    await _require_session(session_id, db)
    items = await notebook_store.list_notebooks(session_id)
    return {"notebooks": items}


# -----------------------------------------------------------------------------
# CRUD on a single notebook (by name)
# -----------------------------------------------------------------------------


@router.post("/sessions/{session_id}/notebooks/{name}/open")
async def open_notebook(
    session_id: str,
    name: str,
    db: AsyncSession = Depends(get_db),
):
    """Open or create a named notebook; pre-warms the shared kernel.

    Every piece of work that is NOT "produce the notebook JSON the client
    needs to render" runs in the background — artifact-row upsert, initial
    save for freshly-created notebooks, and kernel pre-warm. Keeps this
    endpoint fast even when the agent is hammering the Volume with writes.
    """
    import time

    t0 = time.monotonic()
    await _require_session(session_id, db)
    name = notebook_store.sanitize_name(name)
    nb = await notebook_store.load(session_id, name)

    if nb is None:
        # Brand-new notebook. Build it in-memory + seed the cache now so the
        # client sees it immediately; push the save to the Volume in the
        # background (Modal round-trip can be 500ms–2s).
        nb = nbformat.v4.new_notebook()
        notebook_store._cache[(session_id, name)] = nb
        asyncio.create_task(_safe_save(session_id, name))

    # Artifact row upsert happens off the hot path — it's observability-only.
    asyncio.create_task(_safe_ensure_artifact(session_id, name))
    # Pre-warm the kernel without blocking on the (slow) sandbox spawn.
    asyncio.create_task(_safe_prewarm(session_id))

    logger.info(
        "open notebook %s/%s in %d ms",
        session_id[:8],
        name,
        int((time.monotonic() - t0) * 1000),
    )
    return nb


async def _safe_save(session_id: str, name: str) -> None:
    try:
        await notebook_store.save(session_id, name)
    except Exception as e:
        logger.warning("background save failed for %s/%s: %s", session_id, name, e)


async def _safe_ensure_artifact(session_id: str, name: str) -> None:
    from db import async_session

    try:
        async with async_session() as db:
            await _ensure_artifact_row(session_id, name, db)
    except Exception as e:
        logger.warning(
            "background artifact-row upsert failed for %s/%s: %s",
            session_id,
            name,
            e,
        )


async def _safe_prewarm(session_id: str) -> None:
    try:
        await kernel_manager.get_or_create(session_id)
    except Exception as e:
        logger.warning("pre-warm failed for %s: %s", session_id, e)


@router.get("/sessions/{session_id}/notebooks/{name}")
async def get_notebook(
    session_id: str,
    name: str,
    db: AsyncSession = Depends(get_db),
):
    await _require_session(session_id, db)
    nb = await notebook_store.load(session_id, name)
    if nb is None:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return nb


@router.put("/sessions/{session_id}/notebooks/{name}")
async def put_notebook(
    session_id: str,
    name: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    await _require_session(session_id, db)
    try:
        nb = nbformat.from_dict(body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid notebook: {e}")
    merged = notebook_store.apply_source_update(session_id, name, nb)
    await notebook_store.save(session_id, name, merged)
    return {"ok": True}


@router.post("/sessions/{session_id}/notebooks/{name}/cells/{cell_id}/execute")
async def execute_cell(
    session_id: str,
    name: str,
    cell_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    await _require_session(session_id, db)
    code = body.get("code", "")
    if not isinstance(code, str):
        raise HTTPException(status_code=400, detail="`code` must be a string")
    try:
        await kernel_manager.execute(
            session_id,
            cell_id,
            code,
            notebook_name=notebook_store.sanitize_name(name),
        )
    except Exception as e:
        logger.exception("execute cell %s failed", cell_id)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@router.get("/sessions/{session_id}/notebooks/{name}/download")
async def download_notebook(
    session_id: str,
    name: str,
    db: AsyncSession = Depends(get_db),
):
    await _require_session(session_id, db)
    name = notebook_store.sanitize_name(name)
    nb = await notebook_store.load(session_id, name)
    if nb is None:
        raise HTTPException(status_code=404, detail="Notebook not found")
    content = nbformat.writes(nb).encode("utf-8")
    return Response(
        content=content,
        media_type="application/x-ipynb+json",
        headers={"Content-Disposition": f'attachment; filename="{name}.ipynb"'},
    )


# -----------------------------------------------------------------------------
# Session-level kernel controls (notebook-agnostic)
# -----------------------------------------------------------------------------


@router.post("/sessions/{session_id}/notebook/start")
async def start_kernel(session_id: str, db: AsyncSession = Depends(get_db)):
    """Explicitly spin up (or reuse) the session kernel.

    Returns immediately once the spawn is requested; actual readiness is
    reported via the `notebook.kernel.state` SSE stream
    (`starting` → `idle`).
    """
    await _require_session(session_id, db)
    asyncio.create_task(_safe_prewarm(session_id))
    return {"ok": True}


@router.post("/sessions/{session_id}/notebook/interrupt")
async def interrupt(session_id: str, db: AsyncSession = Depends(get_db)):
    await _require_session(session_id, db)
    ok = await kernel_manager.interrupt(session_id)
    return {"ok": ok}


@router.post("/sessions/{session_id}/notebook/shutdown")
async def shutdown(session_id: str, db: AsyncSession = Depends(get_db)):
    await _require_session(session_id, db)
    ok = await kernel_manager.shutdown(session_id)
    return {"ok": ok}


@router.get("/sessions/{session_id}/notebook/status")
async def status(session_id: str, db: AsyncSession = Depends(get_db)):
    await _require_session(session_id, db)
    return kernel_manager.status(session_id)
