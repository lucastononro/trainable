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
        assert (
            await c.get(f"/api/sessions/abc/stream?token={TOKEN}")
        ).status_code == 200
        # ?token= is stream-only — it must not unlock other routes
        assert (await c.get(f"/api/projects?token={TOKEN}")).status_code == 401


@pytest.mark.asyncio
async def test_options_preflight_not_blocked():
    async with _client(TOKEN) as c:
        assert (await c.options("/api/projects")).status_code != 401


@pytest.mark.asyncio
async def test_bearer_scheme_case_insensitive():
    async with _client(TOKEN) as c:
        for scheme in ["bearer", "BEARER", "BeArEr"]:
            resp = await c.get(
                "/api/projects", headers={"Authorization": f"{scheme} {TOKEN}"}
            )
            assert resp.status_code == 200, scheme


@pytest.mark.asyncio
async def test_wrong_scheme_and_malformed_headers_rejected():
    async with _client(TOKEN) as c:
        for auth in [f"Basic {TOKEN}", "Bearer", "Bearer ", TOKEN, ""]:
            resp = await c.get("/api/projects", headers={"Authorization": auth})
            assert resp.status_code == 401, repr(auth)


@pytest.mark.asyncio
async def test_non_ascii_query_token_is_401_not_500():
    # secrets.compare_digest raises TypeError on non-ASCII str inputs; the
    # middleware must compare bytes so garbage tokens 401 instead of 500.
    async with _client(TOKEN) as c:
        resp = await c.get("/api/sessions/abc/stream?token=caf%C3%A9")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_multiple_query_tokens_any_valid_wins():
    async with _client(TOKEN) as c:
        resp = await c.get(f"/api/sessions/abc/stream?token=nope&token={TOKEN}")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_exempt_paths_are_exact_matches():
    async with _client(TOKEN) as c:
        # Not in EXEMPT_PATHS — must still require auth.
        for path in ["/api/healthz", "/api/health/deep", "/api/readyz2"]:
            assert (await c.get(path)).status_code == 401, path


# ---------------------------------------------------------------------------
# Raw-ASGI tests: WebSocket + lifespan scopes (httpx can't drive these)
# ---------------------------------------------------------------------------


class _RecordingApp:
    """Inner ASGI app that records whether the middleware let it run."""

    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True


async def _run_scope(scope: dict) -> tuple[_RecordingApp, list[dict]]:
    inner = _RecordingApp()
    middleware = BearerTokenAuthMiddleware(inner, token=TOKEN)
    sent: list[dict] = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return inner, sent


def _ws_scope(path: str, headers: list | None = None) -> dict:
    return {
        "type": "websocket",
        "path": path,
        "headers": headers or [],
        "query_string": b"",
    }


@pytest.mark.asyncio
async def test_websocket_under_api_rejected_without_token():
    inner, sent = await _run_scope(_ws_scope("/api/ws"))
    assert not inner.called
    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
async def test_websocket_under_api_allowed_with_bearer_header():
    headers = [(b"authorization", f"Bearer {TOKEN}".encode("latin-1"))]
    inner, sent = await _run_scope(_ws_scope("/api/ws", headers))
    assert inner.called
    assert sent == []


@pytest.mark.asyncio
async def test_websocket_outside_api_passes_through():
    inner, _ = await _run_scope(_ws_scope("/ws"))
    assert inner.called


@pytest.mark.asyncio
async def test_lifespan_scope_passes_through():
    inner, _ = await _run_scope({"type": "lifespan"})
    assert inner.called


@pytest.mark.asyncio
async def test_non_ascii_header_token_is_401_not_exception():
    headers = [(b"authorization", b"Bearer caf\xe9")]
    inner, sent = await _run_scope(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/projects",
            "headers": headers,
            "query_string": b"",
        }
    )
    assert not inner.called
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401
