"""Run snapshot endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from schemas import ReproduceRequest, ReproduceReport
from services.reproduce import (
    ManifestUnavailableError,
    NoScriptsError,
    SnapshotNotFoundError,
    reproduce_snapshot,
)
from services.snapshot import get_snapshot, take_snapshot

router = APIRouter()


@router.post("/sessions/{session_id}/snapshot")
async def create_snapshot(session_id: str):
    return await take_snapshot(session_id)


@router.get("/sessions/{session_id}/snapshot")
async def read_snapshot(session_id: str):
    snap = await get_snapshot(session_id)
    if not snap:
        raise HTTPException(status_code=404, detail="No snapshot for this session yet")
    return snap


@router.post(
    "/sessions/{session_id}/snapshot/reproduce", response_model=ReproduceReport
)
async def reproduce(session_id: str, body: ReproduceRequest | None = None):
    """Re-execute the snapshot's captured scripts against the hashed data
    and diff the resulting metrics against the original run, flagging drift."""
    try:
        return await reproduce_snapshot(
            session_id, tolerance=(body or ReproduceRequest()).tolerance
        )
    except SnapshotNotFoundError:
        raise HTTPException(status_code=404, detail="No snapshot for this session yet")
    except (ManifestUnavailableError, NoScriptsError) as e:
        raise HTTPException(status_code=409, detail=str(e))
