"""Prediction playground proxy — POST /api/models/{id}/predict and
GET /api/models/{id}/predict-schema.

The proxy forwards to the live Modal endpoint with the stored X-API-Key
so the browser never holds the key (and never fights Modal CORS). These
tests mock httpx at the service boundary — no network.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from db import async_session
from models import DatasetVersion, Deployment, RegisteredModel

pytestmark = pytest.mark.asyncio


async def _seed_model(
    project_id: str,
    *,
    api_key: str | None = "sk-test-key",
    live_url: str | None = "https://ws--app--fn.modal.run",
    with_train_metadata: bool = False,
) -> str:
    """Insert a RegisteredModel (+ optional live Deployment + training
    DatasetVersion metadata) and return the model id."""
    model_id = str(uuid.uuid4())
    async with async_session() as db:
        dataset_refs = {}
        if with_train_metadata:
            dv = DatasetVersion(
                project_id=project_id,
                kind="processed",
                name="train.csv",
                hash="a" * 64,
                path="/datasets/train.csv",
                dataset_metadata={
                    "feature_columns": ["sepal_length", "sepal_width"],
                    "target_column": "species",
                },
            )
            db.add(dv)
            await db.flush()
            dataset_refs = {"train": {"dataset_id": dv.id, "metrics": {}}}
        db.add(
            RegisteredModel(
                id=model_id,
                project_id=project_id,
                name="iris",
                version=1,
                source_session_id=None,
                artifact_uri="/models/iris/v1/model.pkl",
                framework="sklearn",
                api_key=api_key,
                dataset_refs=dataset_refs,
            )
        )
        if live_url:
            db.add(
                Deployment(
                    id=str(uuid.uuid4()),
                    model_id=model_id,
                    endpoint_url=live_url,
                    status="live",
                    modal_app="app",
                    modal_function="fn",
                )
            )
        await db.commit()
    return model_id


def _mock_httpx_client(status_code: int = 200, json_body=None, text: str = ""):
    """Build a patchable httpx.AsyncClient factory whose post() returns a
    canned response. Returns (client_cls, post_mock)."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text

    post = AsyncMock(return_value=resp)
    client = MagicMock()
    client.post = post
    client_cm = MagicMock()
    client_cm.__aenter__ = AsyncMock(return_value=client)
    client_cm.__aexit__ = AsyncMock(return_value=False)
    client_cls = MagicMock(return_value=client_cm)
    return client_cls, post


async def test_predict_proxy_success_forwards_key(client, default_project_id):
    model_id = await _seed_model(default_project_id, with_train_metadata=True)
    upstream = {"predictions": [0, 1], "model": "iris", "version": 1}
    client_cls, post = _mock_httpx_client(200, upstream)

    with patch("services.deploy.httpx.AsyncClient", client_cls):
        resp = await client.post(
            f"/api/models/{model_id}/predict",
            json={"records": [{"sepal_length": 5.1}, {"sepal_length": 6.2}]},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == upstream
    # The stored key must ride along as X-API-Key, and the body must be
    # the endpoint's native {"records": [...]} contract.
    _, kwargs = post.call_args
    assert kwargs["headers"]["X-API-Key"] == "sk-test-key"
    assert kwargs["json"] == {"records": [{"sepal_length": 5.1}, {"sepal_length": 6.2}]}
    args, _ = post.call_args
    assert args[0] == "https://ws--app--fn.modal.run"


async def test_predict_proxy_no_live_deployment_409(client, default_project_id):
    model_id = await _seed_model(default_project_id, live_url=None)
    resp = await client.post(
        f"/api/models/{model_id}/predict", json={"records": [{"x": 1}]}
    )
    assert resp.status_code == 409
    assert "No live deployment" in resp.json()["detail"]


async def test_predict_proxy_unknown_model_404(client):
    resp = await client.post(
        f"/api/models/{uuid.uuid4()}/predict", json={"records": [{"x": 1}]}
    )
    assert resp.status_code == 404


async def test_predict_proxy_empty_records_400(client, default_project_id):
    model_id = await _seed_model(default_project_id)
    resp = await client.post(f"/api/models/{model_id}/predict", json={"records": []})
    assert resp.status_code == 400


async def test_predict_proxy_over_record_cap_400(client, default_project_id):
    """Batches above PREDICT_PROXY_MAX_RECORDS are rejected before any
    upstream call — the playground is for smoke tests, not batch scoring."""
    from services import deploy as deploy_svc

    model_id = await _seed_model(default_project_id)
    records = [{"x": i} for i in range(deploy_svc.PREDICT_PROXY_MAX_RECORDS + 1)]
    client_cls, post = _mock_httpx_client(200, {"predictions": []})
    with patch("services.deploy.httpx.AsyncClient", client_cls):
        resp = await client.post(
            f"/api/models/{model_id}/predict", json={"records": records}
        )
    assert resp.status_code == 400
    assert "Too many records" in resp.json()["detail"]
    post.assert_not_called()


async def test_predict_proxy_network_error_502(client, default_project_id):
    """An unreachable endpoint (timeout, DNS, connection refused) becomes
    a 502 with the cold-start retry hint, not an unhandled exception."""
    import httpx as _httpx

    model_id = await _seed_model(default_project_id)
    client_cls, post = _mock_httpx_client(200, {})
    post.side_effect = _httpx.ConnectTimeout("timed out")
    with patch("services.deploy.httpx.AsyncClient", client_cls):
        resp = await client.post(
            f"/api/models/{model_id}/predict", json={"records": [{"x": 1}]}
        )
    assert resp.status_code == 502
    assert "Could not reach the deployed endpoint" in resp.json()["detail"]


async def test_predict_proxy_passes_upstream_401_through(client, default_project_id):
    """Key drift (rotated key + stale container) surfaces as the
    upstream 401, not a generic proxy error."""
    model_id = await _seed_model(default_project_id)
    client_cls, _ = _mock_httpx_client(401, {"detail": "Invalid or missing X-API-Key"})
    with patch("services.deploy.httpx.AsyncClient", client_cls):
        resp = await client.post(
            f"/api/models/{model_id}/predict", json={"records": [{"x": 1}]}
        )
    assert resp.status_code == 401
    assert "X-API-Key" in resp.json()["detail"]


async def test_predict_proxy_upstream_500_becomes_502(client, default_project_id):
    model_id = await _seed_model(default_project_id)
    client_cls, _ = _mock_httpx_client(500, {"detail": "model failed to load"})
    with patch("services.deploy.httpx.AsyncClient", client_cls):
        resp = await client.post(
            f"/api/models/{model_id}/predict", json={"records": [{"x": 1}]}
        )
    assert resp.status_code == 502
    assert "model failed to load" in resp.json()["detail"]


async def test_predict_schema_resolves_feature_columns(client, default_project_id):
    model_id = await _seed_model(default_project_id, with_train_metadata=True)
    resp = await client.get(f"/api/models/{model_id}/predict-schema")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["feature_columns"] == ["sepal_length", "sepal_width"]
    assert body["target_column"] == "species"
    assert body["has_live_deployment"] is True
    assert body["endpoint_url"] == "https://ws--app--fn.modal.run"


async def test_predict_schema_without_metadata_is_null(client, default_project_id):
    model_id = await _seed_model(default_project_id, live_url=None)
    resp = await client.get(f"/api/models/{model_id}/predict-schema")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_columns"] is None
    assert body["has_live_deployment"] is False


async def test_predict_schema_unknown_model_404(client):
    resp = await client.get(f"/api/models/{uuid.uuid4()}/predict-schema")
    assert resp.status_code == 404


async def test_generate_serving_app_still_uses_metadata(default_project_id):
    """Regression guard for the refactor: generate_serving_app must still
    embed the resolved feature columns in the rendered app.py."""
    from services import deploy as deploy_svc

    model_id = await _seed_model(
        default_project_id, live_url=None, with_train_metadata=True
    )
    written: dict = {}

    async def fake_write(content, path):
        written["content"] = content
        written["path"] = path

    with patch("services.volume.write_to_volume", side_effect=fake_write):
        out = await deploy_svc.generate_serving_app(model_id)

    assert "sepal_length" in written["content"]
    assert out["serving_app_path"] == written["path"]
    async with async_session() as db:
        m = (
            await db.execute(
                select(RegisteredModel).where(RegisteredModel.id == model_id)
            )
        ).scalar_one()
        assert m.serving_app_path == written["path"]
