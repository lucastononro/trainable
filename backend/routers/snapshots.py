"""Run snapshot endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

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
