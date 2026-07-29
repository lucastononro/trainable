"""Tests for the HTML canvas artifact pipeline.

Covers:
- `services.canvas.publish_canvas_html` — SSE shape + Message persistence
- `/files/raw` CSP headers on text/html responses (sandbox-iframe contract)
- The `show-html` skill handler — happy path + scoping + size cap rejection

Note: the old metrics-parser `canvas_html` envelope was removed; the
agent now publishes HTML via the `show-html` skill, never via stdout.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# services.canvas.publish_canvas_html
# ---------------------------------------------------------------------------


class TestPublishCanvasHtml:
    @pytest.mark.asyncio
    async def test_publishes_sse_event_and_persists_message(self, setup_db):
        from services.canvas import publish_canvas_html

        with patch("services.canvas.broadcaster") as mock_broadcaster:
            mock_broadcaster.publish = AsyncMock()
            payload = await publish_canvas_html(
                "sess-1",
                key="demo",
                title="Demo",
                path="/sessions/sess-1/canvas/demo.html",
                size=1024,
                ts=1700000000.0,
                stage="trainer",
            )

            assert mock_broadcaster.publish.call_count == 1
            args = mock_broadcaster.publish.call_args
            assert args[0][0] == "sess-1"
            event = args[0][1]
            assert event["type"] == "canvas_html"
            assert event["data"]["key"] == "demo"
            assert event["data"]["title"] == "Demo"
            assert event["data"]["path"] == "/sessions/sess-1/canvas/demo.html"
            assert event["data"]["size"] == 1024
            assert event["data"]["stage"] == "trainer"

            assert payload["key"] == "demo"
            assert payload["type"] == "html"


# ---------------------------------------------------------------------------
# /files/raw — CSP + nosniff headers for text/html
# ---------------------------------------------------------------------------


class TestFilesRawHtmlHeaders:
    def _client(self):
        from main import app

        return TestClient(app)

    def test_html_response_has_csp_and_nosniff(self):
        client = self._client()
        payload = b"<html><body><h1>hi</h1></body></html>"
        with patch(
            "routers.files.read_volume_file_async", new=AsyncMock(return_value=payload)
        ):
            resp = client.get(
                "/api/files/raw", params={"path": "/sessions/abc/canvas/x.html"}
            )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/html")
        csp = resp.headers.get("content-security-policy", "")
        assert csp, "CSP header must be present on text/html responses"
        assert "default-src 'none'" in csp
        # 'self' allows companion JS/CSS files saved alongside the HTML
        # to load via absolute /api/files/raw?path=… URLs.
        assert "script-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "connect-src 'none'" in csp
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("referrer-policy") == "no-referrer"

    def test_non_html_response_has_no_csp(self):
        client = self._client()
        payload = b"\x89PNG\r\n\x1a\n"
        with patch(
            "routers.files.read_volume_file_async", new=AsyncMock(return_value=payload)
        ):
            resp = client.get(
                "/api/files/raw", params={"path": "/sessions/abc/figures/x.png"}
            )
        assert resp.status_code == 200
        assert "content-security-policy" not in {k.lower() for k in resp.headers.keys()}


# ---------------------------------------------------------------------------
# Skill: show-html handler
# ---------------------------------------------------------------------------


def _import_show_html_handler():
    """Skills folders use hyphens, so we need importlib.util to load it."""
    import importlib.util

    skill_path = (
        Path(__file__).resolve().parent.parent / "skills" / "show-html" / "handler.py"
    )
    spec = importlib.util.spec_from_file_location("show_html_handler", skill_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestShowHtmlSkill:
    @pytest.mark.asyncio
    async def test_happy_path_publishes_artifact(self, setup_db):
        mod = _import_show_html_handler()
        handler = mod.create_handler(session_id="sess-1", publish_fn=None)
        body = b"<html><body><h1>hi</h1></body></html>"

        with (
            patch("services.canvas.broadcaster") as mock_broadcaster,
            patch.object(mod, "reload_volume_async", new=AsyncMock(return_value=True)),
            patch.object(
                mod, "read_volume_file_async", new=AsyncMock(return_value=body)
            ),
        ):
            mock_broadcaster.publish = AsyncMock()
            result = await handler(
                {
                    "path": "/sessions/sess-1/canvas/demo.html",
                    "title": "Demo",
                    "key": "demo",
                }
            )

        assert "is_error" not in result or result.get("is_error") is False
        assert mock_broadcaster.publish.call_count == 1
        event = mock_broadcaster.publish.call_args[0][1]
        assert event["type"] == "canvas_html"
        assert event["data"]["key"] == "demo"
        assert event["data"]["title"] == "Demo"
        assert event["data"]["size"] == len(body)

    @pytest.mark.asyncio
    async def test_path_outside_session_rejected(self):
        mod = _import_show_html_handler()
        handler = mod.create_handler(session_id="sess-1", publish_fn=None)

        # No volume read should happen — the path check must trip first.
        with (
            patch.object(mod, "reload_volume_async", new=AsyncMock()) as r,
            patch.object(mod, "read_volume_file_async", new=AsyncMock()) as rf,
        ):
            result = await handler(
                {"path": "/sessions/other-sid/canvas/demo.html", "title": "x"}
            )
            r.assert_not_called()
            rf.assert_not_called()

        assert result["is_error"] is True
        assert "must be inside" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_non_html_path_rejected(self):
        mod = _import_show_html_handler()
        handler = mod.create_handler(session_id="sess-1", publish_fn=None)

        with (
            patch.object(mod, "reload_volume_async", new=AsyncMock()),
            patch.object(mod, "read_volume_file_async", new=AsyncMock()),
        ):
            result = await handler({"path": "/sessions/sess-1/canvas/demo.md"})

        assert result["is_error"] is True
        assert ".html" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_oversize_payload_rejected(self):
        mod = _import_show_html_handler()
        handler = mod.create_handler(session_id="sess-1", publish_fn=None)
        oversize = b"x" * (mod._MAX_HTML_BYTES + 1)

        with (
            patch("services.canvas.broadcaster") as mock_broadcaster,
            patch.object(mod, "reload_volume_async", new=AsyncMock()),
            patch.object(
                mod, "read_volume_file_async", new=AsyncMock(return_value=oversize)
            ),
        ):
            mock_broadcaster.publish = AsyncMock()
            result = await handler({"path": "/sessions/sess-1/canvas/big.html"})

        assert result["is_error"] is True
        assert "exceeds" in result["content"][0]["text"]
        mock_broadcaster.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_path_rejected(self):
        mod = _import_show_html_handler()
        handler = mod.create_handler(session_id="sess-1", publish_fn=None)
        result = await handler({})
        assert result["is_error"] is True
        assert "required" in result["content"][0]["text"]
