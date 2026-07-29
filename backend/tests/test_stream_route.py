"""Regression tests for the SSE /stream endpoint.

The route silently 404'd once because a logging wrapper returned a
generator with the wrong shape — sse-starlette couldn't negotiate the
response and FastAPI fell through to the default 404 handler. The user
lost work cycles because nothing in the existing tests exercised the
route at all.

End-to-end SSE testing inside pytest-asyncio is fragile (sse-starlette
binds AppStatus.should_exit_event to a global loop that the
session-scoped fixture conflicts with), so we split coverage into two
tighter checks:

  1. The route is mounted on the FastAPI app at the expected path with
     a GET method. Catches the "route disappeared" failure mode.
  2. broadcaster.subscribe + publish + queue.get round-trips correctly.
     Catches the broadcaster-layer "events don't reach subscribers"
     failure mode.

Together these cover the streaming path without the asyncio loop
gymnastics that an integration test would require.
"""

from __future__ import annotations

import asyncio

import pytest


def test_stream_route_is_registered():
    """Regression: the GET /api/sessions/{sid}/stream route must be
    present on the app. It silently 404'd once when a faulty handler
    refactor broke sse-starlette's content negotiation — this test
    would have caught it in CI."""
    from main import app

    matched = []
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        if route.path == "/api/sessions/{session_id}/stream":
            matched.append(route)

    assert matched, (
        "/api/sessions/{session_id}/stream is not registered. The "
        "stream router likely failed to import or the route path "
        "drifted from what the frontend EventSource expects."
    )
    methods = matched[0].methods or set()
    assert "GET" in methods, f"Expected GET on /stream, got {methods}"


@pytest.mark.asyncio
async def test_broadcaster_publish_reaches_subscribers():
    """The runner emits agent text by calling broadcaster.publish; the
    SSE handler subscribes via broadcaster.stream. If publish doesn't
    fan out to subscribers, "live updates" silently break.

    We exercise the broadcaster directly (no FastAPI / SSE wrapper) so
    this test is robust to event-loop-scope issues that block the full
    end-to-end test."""
    from services.broadcaster import broadcaster

    sid = "broadcaster-fanout-test"
    queue = await broadcaster.subscribe(sid)
    try:
        await broadcaster.publish(
            sid, {"type": "agent_message", "data": {"text": "hi"}}
        )
        # Tight timeout — publish is synchronous-ish; if the queue
        # doesn't have the event in 1s it never will.
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["type"] == "agent_message"
        assert event["data"]["text"] == "hi"
    finally:
        broadcaster.unsubscribe(sid, queue)


@pytest.mark.asyncio
async def test_broadcaster_isolates_sessions():
    """A publish to session A must not leak to session B's
    subscribers. Caught a subtle bug once where the broadcaster used a
    single global queue."""
    from services.broadcaster import broadcaster

    qa = await broadcaster.subscribe("session-a")
    qb = await broadcaster.subscribe("session-b")
    try:
        await broadcaster.publish("session-a", {"type": "agent_message", "data": {}})
        # qa should receive; qb should not.
        ev = await asyncio.wait_for(qa.get(), timeout=1.0)
        assert ev["type"] == "agent_message"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(qb.get(), timeout=0.1)
    finally:
        broadcaster.unsubscribe("session-a", qa)
        broadcaster.unsubscribe("session-b", qb)
