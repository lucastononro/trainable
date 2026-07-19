"""Opt-in bearer-token auth middleware tests (backend/auth.py)."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from auth import BearerTokenAuthMiddleware

TOKEN = "test-secret-token"


def _make_app(token: str | None) -> FastAPI:
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/readyz")
    async def readyz():
        return {"status": "ready"}

    @app.get("/api/projects")
    async def projects():
        return []

    @app.get("/api/sessions/{session_id}/stream")
    async def stream(session_id: str):
        return {"session_id": session_id}

    if token:
        app.add_middleware(BearerTokenAuthMiddleware, token=token)
    return app


def _client(token: str | None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=_make_app(token)), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_no_token_configured_everything_open():
    async with _client(None) as c:
        for path in ["/api/health", "/api/projects", "/api/sessions/x/stream"]:
            assert (await c.get(path)).status_code == 200


@pytest.mark.asyncio
async def test_token_set_requires_bearer():
    async with _client(TOKEN) as c:
        resp = await c.get("/api/projects")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"

        bad = await c.get("/api/projects", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401

        ok = await c.get("/api/projects", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200


@pytest.mark.asyncio
async def test_health_and_readyz_exempt():
    async with _client(TOKEN) as c:
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/api/readyz")).status_code == 200


@pytest.mark.asyncio
async def test_stream_accepts_query_token():
    async with _client(TOKEN) as c:
        assert (await c.get("/api/sessions/abc/stream")).status_code == 401
        assert (await c.get("/api/sessions/abc/stream?token=nope")).status_code == 401
        assert (await c.get(f"/api/sessions/abc/stream?token={TOKEN}")).status_code == 200
        # ?token= is stream-only — it must not unlock other routes
        assert (await c.get(f"/api/projects?token={TOKEN}")).status_code == 401


@pytest.mark.asyncio
async def test_options_preflight_not_blocked():
    async with _client(TOKEN) as c:
        assert (await c.options("/api/projects")).status_code != 401
