"""Canvas artifact publishing — HTML/JS showcases the agent surfaces on
the workspace canvas.

This is the *non-metrics* side of the canvas surface. Reports
(`report_ready`) and rich-log panels (`log_event`) target the structured
metrics dashboard; HTML artifacts published here are arbitrary
presentation pages (Plotly dashboards, ydata-profiling reports, custom
HTML/JS apps) rendered in a sandboxed iframe.

Kept in its own module so it doesn't get tangled with the metrics
parser pipeline — agents publish HTML via the `show-html` skill, not via
stdout envelopes.
"""

from __future__ import annotations

import logging

from db import async_session
from models import Message
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)


async def publish_canvas_html(
    session_id: str,
    *,
    key: str,
    title: str,
    path: str,
    size: int | None = None,
    ts: float | None = None,
    stage: str | None = None,
) -> dict:
    """Publish a canvas_html SSE event and persist a system Message so the
    artifact survives session reload.

    The HTML body lives on the volume at `path` and is fetched via
    `/api/files/raw` on render — we don't carry it through the DB.
    Returns the payload that was broadcast (useful for tool-result
    construction in the calling skill).
    """
    payload = {
        "type": "html",
        "key": key,
        "title": title or key,
        "path": path,
        "size": size,
        "ts": ts,
        "stage": stage,
    }
    await broadcaster.publish(session_id, {"type": "canvas_html", "data": payload})

    try:
        async with async_session() as db:
            db.add(
                Message(
                    session_id=session_id,
                    role="system",
                    content="",
                    metadata_={"event_type": "canvas_html", **payload},
                )
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to persist canvas_html: %s", e)

    return payload
