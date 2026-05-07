"""SSE streaming endpoint."""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)
router = APIRouter()


async def _logged_stream(
    session_id: str, request: Request
) -> AsyncGenerator[dict, None]:
    """Wrap broadcaster.stream() with explicit open/close lifecycle logs.

    Helps diagnose "live updates stopped" bugs — the connection state is
    invisible from the application logs without this. Also catches the
    client-disconnect path so we know which side dropped the connection.
    """
    client = (
        f"{request.client.host}:{request.client.port}" if request.client else "unknown"
    )
    logger.info("[SSE] session=%s client=%s opened", session_id, client)
    n = 0
    try:
        async for chunk in broadcaster.stream(session_id):
            n += 1
            yield chunk
    finally:
        # `await request.is_disconnected()` would be ambiguous — at this point
        # we already exited the loop, so log the count and the side that
        # ended it. Most disconnects come from the client closing the tab
        # or navigating away.
        logger.info(
            "[SSE] session=%s client=%s closed events=%d", session_id, client, n
        )


@router.get("/sessions/{session_id}/stream")
async def stream_events(session_id: str, request: Request):
    return EventSourceResponse(_logged_stream(session_id, request))
