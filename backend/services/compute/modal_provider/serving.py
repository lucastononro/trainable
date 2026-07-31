"""Modal implementation of ServingBackend.

Thin adapter over the Modal-specific helpers that live in
services/deploy.py (`_serving_app_code`, `_run_modal_deploy`,
`_ensure_modal_secret`, `_run_modal_app_stop`, naming). Those helpers stay
defined on the deploy module — tests exercise them by name and the
generated app.py format is documented there — this class just gives them
the provider-neutral interface.
"""

from __future__ import annotations

from config import settings
from services.compute.base import DeployResult, ServingBackend


class ModalServingBackend(ServingBackend):
    name = "modal"
    serving_filename = "app.py"

    def app_name(self, project_id: str) -> str:
        from services import deploy

        return deploy._modal_app_name(project_id)

    def fn_name(self, model_name: str, version: int) -> str:
        from services import deploy

        return deploy._modal_function_name(model_name, version)

    def render_serving_code(
        self,
        *,
        app_name: str,
        fn_name: str,
        model_name: str,
        model_version: int,
        artifact_uri: str,
        framework: str,
        feature_columns: list[str] | None,
        target_column: str | None,
        compute: str,
        enable_auth: bool,
        model_id: str,
    ) -> str:
        from services import deploy

        secret_name = deploy._api_secret_name(model_id) if enable_auth else None
        return deploy._serving_app_code(
            app_name=app_name,
            fn_name=fn_name,
            model_name=model_name,
            model_version=model_version,
            artifact_uri=artifact_uri,
            framework=framework,
            feature_columns=feature_columns,
            target_column=target_column,
            volume_name=settings.modal_volume_name,
            compute=compute,
            api_secret_name=secret_name,
        )

    async def ensure_secret(self, model_id: str, api_key: str) -> str | None:
        from services import deploy

        secret_name = deploy._api_secret_name(model_id)
        await deploy._ensure_modal_secret(secret_name, api_key)
        return secret_name

    async def deploy(
        self,
        *,
        model,
        serving_app_path: str,
        compute: str,
        api_key: str | None,
    ) -> DeployResult:
        from services import deploy

        app_name = self.app_name(model.project_id)
        fn_name = self.fn_name(model.name, model.version)
        url = await deploy._run_modal_deploy(serving_app_path, app_name)
        return DeployResult(
            endpoint_url=url, provider_app=app_name, provider_function=fn_name
        )

    async def stop(self, deployment_row) -> None:
        from services import deploy

        if deployment_row.modal_app:
            await deploy._run_modal_app_stop(deployment_row.modal_app)

    async def rotate_key(self, model_id: str, new_key: str) -> str | None:
        return await self.ensure_secret(model_id, new_key)
