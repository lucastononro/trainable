"""Tests for the RunPod serving backend — handler codegen, endpoint
lifecycle, teardown. The RunPod client is a canned fake."""

import ast
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import settings
from services.compute.runpod_provider import serving as rp_serving


class FakeClient:
    def __init__(self, templates=None, endpoints=None):
        self.templates = templates or []
        self.endpoints = endpoints or []
        self.created_templates = []
        self.updated_templates = []
        self.created_endpoints = []
        self.updated_endpoints = []
        self.deleted_endpoints = []

    async def list_templates(self):
        return self.templates

    async def create_template(self, payload):
        self.created_templates.append(payload)
        return {"id": f"tpl-{len(self.created_templates)}"}

    async def update_template(self, template_id, payload):
        self.updated_templates.append((template_id, payload))
        return {"id": template_id}

    async def list_endpoints(self):
        return self.endpoints

    async def create_endpoint(self, payload):
        self.created_endpoints.append(payload)
        return {"id": f"ep-{len(self.created_endpoints)}"}

    async def update_endpoint(self, endpoint_id, payload):
        self.updated_endpoints.append((endpoint_id, payload))
        return {"id": endpoint_id}

    async def delete_endpoint(self, endpoint_id):
        self.deleted_endpoints.append(endpoint_id)


def _model():
    return SimpleNamespace(
        id="m-1",
        project_id="abcdef012345-9999-8888",
        name="churn model",
        version=2,
        artifact_uri="/data/sessions/s1/models/churn.pkl",
        framework="sklearn",
        api_key="secret-key",
        serving_app_path="/projects/p/models/churn model/v2/handler.py",
    )


@pytest.fixture
def backend():
    return rp_serving.RunPodServingBackend()


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(rp_serving, "get_client", lambda: client)
    monkeypatch.setattr(
        rp_serving, "ensure_network_volume", AsyncMock(return_value="vol-1")
    )
    return client


class TestHandlerCodegen:
    def _render(self, backend, **overrides):
        kwargs = dict(
            app_name="trainable-srv-abcdef012345",
            fn_name="churn-model-v2",
            model_name="churn model",
            model_version=2,
            artifact_uri="/data/sessions/s1/models/churn.pkl",
            framework="sklearn",
            feature_columns=["age", "tenure"],
            target_column="churned",
            compute="cpu",
            enable_auth=True,
            model_id="m-1",
        )
        kwargs.update(overrides)
        return backend.render_serving_code(**kwargs)

    def test_generated_handler_parses(self, backend):
        code = self._render(backend)
        ast.parse(code)

    def test_api_key_gate_present_when_auth_enabled(self, backend):
        code = self._render(backend)
        assert 'inp.get("api_key") != API_KEY' in code

    def test_api_key_gate_absent_when_auth_disabled(self, backend):
        code = self._render(backend, enable_auth=False)
        ast.parse(code)
        assert "api_key" not in code.split('"""', 2)[2]  # body has no gate

    def test_artifact_path_not_double_prefixed(self, backend):
        code = self._render(backend)
        assert "'/data/sessions/s1/models/churn.pkl'" in code
        assert "/data/data" not in code

    def test_xgboost_native_loader_selected(self, backend):
        code = self._render(
            backend,
            framework="xgboost",
            artifact_uri="/sessions/s1/models/model.json",
        )
        ast.parse(code)
        assert "xgb.Booster()" in code


class TestDeploy:
    @pytest.mark.asyncio
    async def test_first_deploy_creates_template_and_endpoint(self, backend, fake):
        result = await backend.deploy(
            model=_model(),
            serving_app_path="/projects/p/models/churn model/v2/handler.py",
            compute="T4",
            api_key="secret-key",
        )

        assert result.endpoint_id == "ep-1"
        assert result.endpoint_url == "https://api.runpod.ai/v2/ep-1/runsync"

        tpl = fake.created_templates[0]
        assert tpl["env"]["TRAINABLE_ROLE"] == "serving"
        assert tpl["env"]["API_KEY"] == "secret-key"
        assert tpl["env"]["HANDLER_PATH"].startswith("/data/projects/")

        ep = fake.created_endpoints[0]
        assert ep["workersMin"] == 0
        assert ep["networkVolumeId"] == "vol-1"
        assert ep["gpuTypeIds"]  # T4 maps to a GPU pool
        assert ep["dataCenterIds"] == [settings.runpod_datacenter_id]

    @pytest.mark.asyncio
    async def test_cpu_deploy_uses_cpu_compute(self, backend, fake):
        await backend.deploy(
            model=_model(),
            serving_app_path="/p/handler.py",
            compute="cpu",
            api_key=None,
        )
        ep = fake.created_endpoints[0]
        assert "gpuTypeIds" not in ep
        assert ep["computeType"] == "CPU"

    @pytest.mark.asyncio
    async def test_redeploy_patches_existing_endpoint(self, backend, fake, monkeypatch):
        model = _model()
        name = backend._endpoint_name(model.project_id, model.name, model.version)
        fake.endpoints = [{"name": name, "id": "ep-existing"}]

        result = await backend.deploy(
            model=model,
            serving_app_path="/p/handler.py",
            compute="cpu",
            api_key="k",
        )
        assert result.endpoint_id == "ep-existing"
        assert fake.created_endpoints == []
        endpoint_id, payload = fake.updated_endpoints[0]
        assert endpoint_id == "ep-existing"
        assert "name" not in payload


class TestStopAndRotate:
    @pytest.mark.asyncio
    async def test_stop_scales_down_then_deletes(self, backend, fake):
        row = SimpleNamespace(provider_endpoint_id="ep-9", modal_app="x")
        await backend.stop(row)
        assert fake.updated_endpoints[0] == ("ep-9", {"workersMax": 0})
        assert fake.deleted_endpoints == ["ep-9"]

    @pytest.mark.asyncio
    async def test_stop_without_endpoint_id_raises(self, backend, fake):
        row = SimpleNamespace(provider_endpoint_id=None, modal_app="x")
        with pytest.raises(RuntimeError, match="provider_endpoint_id"):
            await backend.stop(row)
