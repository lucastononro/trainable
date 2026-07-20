"""Readiness probe tests (/api/readyz)."""

from unittest.mock import MagicMock

import pytest

import main


class _FailingConn:
    async def __aenter__(self):
        raise ConnectionError("db down")

    async def __aexit__(self, *args):
        return False


def _s3_client_with_broken_list_buckets():
    """Client construction succeeds; the actual API call fails (real outage shape)."""
    mock_client = MagicMock()
    mock_client.list_buckets.side_effect = ConnectionError("s3 down")
    return mock_client


@pytest.mark.asyncio
async def test_readyz_ok(client, monkeypatch):
    """DB (in-memory sqlite) + mocked S3 up -> 200 ready."""
    monkeypatch.setattr(main, "get_s3_client", lambda: MagicMock())
    resp = await client.get("/api/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "checks": {"database": "ok", "s3": "ok"}}


@pytest.mark.asyncio
async def test_readyz_s3_down(client, monkeypatch):
    """Real outage shape: client builds fine, list_buckets raises inside to_thread."""
    monkeypatch.setattr(main, "get_s3_client", _s3_client_with_broken_list_buckets)
    resp = await client.get("/api/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["s3"] == "error: ConnectionError"


@pytest.mark.asyncio
async def test_readyz_s3_client_construction_fails(client, monkeypatch):
    """get_s3_client() itself raising (e.g. bad config) is also caught -> 503."""

    def broken_s3():
        raise ConnectionError("s3 down")

    monkeypatch.setattr(main, "get_s3_client", broken_s3)
    resp = await client.get("/api/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["s3"].startswith("error:")


@pytest.mark.asyncio
async def test_readyz_db_down(client, monkeypatch):
    monkeypatch.setattr(main, "get_s3_client", lambda: MagicMock())
    fake_engine = MagicMock()
    fake_engine.connect = lambda: _FailingConn()
    monkeypatch.setattr(main, "engine", fake_engine)
    resp = await client.get("/api/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"].startswith("error:")
    assert body["checks"]["s3"] == "ok"


@pytest.mark.asyncio
async def test_readyz_both_down(client, monkeypatch):
    """Checks run concurrently (asyncio.gather) — one failure must not mask the other."""
    monkeypatch.setattr(main, "get_s3_client", _s3_client_with_broken_list_buckets)
    fake_engine = MagicMock()
    fake_engine.connect = lambda: _FailingConn()
    monkeypatch.setattr(main, "engine", fake_engine)
    resp = await client.get("/api/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"].startswith("error:")
    assert body["checks"]["s3"] == "error: ConnectionError"


@pytest.mark.asyncio
async def test_health_stays_static(client):
    """Liveness stays cheap — static 200, no dependency checks."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
