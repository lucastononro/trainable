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

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if (
            not path.startswith("/api/")
            or path in EXEMPT_PATHS
            or scope["method"] == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return

        if self._authorized(scope):
            await self.app(scope, receive, send)
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
                    credentials.strip(), self.token
                ):
                    return True
                break

        # EventSource cannot send headers — accept ?token= on the SSE stream.
        if _is_stream_path(scope["path"]):
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            for candidate in query.get("token", []):
                if secrets.compare_digest(candidate, self.token):
                    return True

        return False
