"""SSE streaming endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/sessions/{session_id}/stream")
async def stream_events(session_id: str, request: Request):
    """SSE endpoint for live session events.

    Logs open/close at INFO level (helps diagnose "live updates stopped"
    bugs without ad-hoc prints) and yields the broadcaster's events
    directly. The previous implementation wrapped broadcaster.stream()
    in a generator that took `request` as a parameter — that broke
    sse_starlette's content negotiation and made the route 404 in
    practice. The fix is to keep `EventSourceResponse(broadcaster.stream(
    session_id))` as the body and do the logging in this outer handler
    instead.
    """
    client = (
        f"{request.client.host}:{request.client.port}" if request.client else "unknown"
    )
    logger.info("[SSE] session=%s client=%s opened", session_id, client)
    return EventSourceResponse(broadcaster.stream(session_id))
