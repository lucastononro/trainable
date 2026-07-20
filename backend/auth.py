"""Opt-in bearer-token auth for the API.

When ``API_AUTH_TOKEN`` is unset (the default) this module does nothing —
every endpoint stays open, exactly as before. When it is set, all ``/api/*``
requests must carry ``Authorization: Bearer <token>``.

Exemptions (always open):
- ``/api/health`` and ``/api/readyz`` — probes must never need credentials.
- CORS preflight (``OPTIONS``) requests — browsers send them without headers.

SSE exception: the browser ``EventSource`` API cannot set an Authorization
header, so the stream endpoint (``/api/sessions/{id}/stream``) additionally
accepts the token via a ``?token=<token>`` query parameter.

.. warning::
    Unlike the Authorization header, a ``?token=`` query parameter is part
    of the URL and is written verbatim to access logs by uvicorn, nginx,
    and most proxies/load balancers. When ``API_AUTH_TOKEN`` is set and the
    stream endpoint is used through such a component, configure log
    redaction for the ``token`` query parameter (e.g. uvicorn
    ``--no-access-log`` / a custom access-log format, or nginx log-format
    masking) or restrict who can read the logs.

WebSocket scopes under ``/api/`` are gated by the same rules (no such
routes exist today; unauthenticated handshakes are rejected with close
code 1008 so a future route cannot silently ship open).
"""

import secrets
from urllib.parse import parse_qs

EXEMPT_PATHS = {"/api/health", "/api/readyz"}


def _is_stream_path(path: str) -> bool:
    return path.startswith("/api/sessions/") and path.endswith("/stream")


class BearerTokenAuthMiddleware:
    """Pure ASGI middleware — avoids BaseHTTPMiddleware response wrapping,
    which can interfere with SSE streaming."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token
        # Compare as bytes: secrets.compare_digest raises TypeError on
        # non-ASCII str inputs, which would turn a garbage token into a 500.
        self._token_bytes = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if (
            not path.startswith("/api/")
            # WebSocket scopes have no "method" key.
            or path in EXEMPT_PATHS
            or scope.get("method") == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return

        if self._authorized(scope):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Reject the handshake before accepting (1008 = policy violation).
            await send({"type": "websocket.close", "code": 1008})
            return

        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"detail":"Not authenticated"}',
            }
        )

    def _authorized(self, scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                auth = value.decode("latin-1")
                scheme, _, credentials = auth.partition(" ")
                if scheme.lower() == "bearer" and secrets.compare_digest(
                    credentials.strip().encode("utf-8"), self._token_bytes
                ):
                    return True
                break

        # EventSource cannot send headers — accept ?token= on the SSE stream.
        if _is_stream_path(scope["path"]):
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            for candidate in query.get("token", []):
                if secrets.compare_digest(candidate.encode("utf-8"), self._token_bytes):
                    return True

        return False
