"""Async HTTP client for the RunPod APIs.

Two planes:
  control  https://rest.runpod.io/v1   pods / templates / endpoints / volumes
  data     https://api.runpod.ai/v2    serverless job submit / stream / status

Both authenticate with `Authorization: Bearer <RUNPOD_API_KEY>`. We talk
to them directly with httpx (async-native); the `runpod` SDK is only used
inside the worker image. All methods raise RunPodAPIError with the
response body on non-2xx so callers get actionable messages.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

REST_BASE = "https://rest.runpod.io/v1"
DATA_BASE = "https://api.runpod.ai/v2"

_DEFAULT_TIMEOUT = httpx.Timeout(30.0, read=90.0)


class RunPodAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RunPodClient:
    """Thin wrapper: one shared AsyncClient, bearer auth, error surfacing."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key if api_key is not None else settings.runpod_api_key
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> Any:
        resp = await self._http().request(method, url, **kwargs)
        if resp.status_code >= 400:
            body = resp.text[:1000]
            raise RunPodAPIError(
                f"RunPod API {method} {url} -> {resp.status_code}: {body}",
                status_code=resp.status_code,
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- control plane ------------------------------------------------------

    async def list_endpoints(self) -> list[dict]:
        data = await self._request("GET", f"{REST_BASE}/endpoints")
        return data if isinstance(data, list) else (data or {}).get("endpoints", [])

    async def create_endpoint(self, payload: dict) -> dict:
        return await self._request("POST", f"{REST_BASE}/endpoints", json=payload)

    async def update_endpoint(self, endpoint_id: str, payload: dict) -> dict:
        return await self._request(
            "PATCH", f"{REST_BASE}/endpoints/{endpoint_id}", json=payload
        )

    async def delete_endpoint(self, endpoint_id: str) -> None:
        await self._request("DELETE", f"{REST_BASE}/endpoints/{endpoint_id}")

    async def list_templates(self) -> list[dict]:
        data = await self._request("GET", f"{REST_BASE}/templates")
        return data if isinstance(data, list) else (data or {}).get("templates", [])

    async def create_template(self, payload: dict) -> dict:
        return await self._request("POST", f"{REST_BASE}/templates", json=payload)

    async def update_template(self, template_id: str, payload: dict) -> dict:
        return await self._request(
            "PATCH", f"{REST_BASE}/templates/{template_id}", json=payload
        )

    async def get_endpoint(self, endpoint_id: str) -> dict:
        return await self._request("GET", f"{REST_BASE}/endpoints/{endpoint_id}")

    async def list_network_volumes(self) -> list[dict]:
        data = await self._request("GET", f"{REST_BASE}/networkvolumes")
        return (
            data if isinstance(data, list) else (data or {}).get("networkVolumes", [])
        )

    async def create_network_volume(self, payload: dict) -> dict:
        return await self._request("POST", f"{REST_BASE}/networkvolumes", json=payload)

    async def create_pod(self, payload: dict) -> dict:
        return await self._request("POST", f"{REST_BASE}/pods", json=payload)

    async def get_pod(self, pod_id: str) -> dict:
        return await self._request("GET", f"{REST_BASE}/pods/{pod_id}")

    async def delete_pod(self, pod_id: str) -> None:
        await self._request("DELETE", f"{REST_BASE}/pods/{pod_id}")

    # -- serverless data plane ----------------------------------------------

    async def run(self, endpoint_id: str, payload: dict) -> dict:
        return await self._request(
            "POST", f"{DATA_BASE}/{endpoint_id}/run", json=payload
        )

    async def stream(self, endpoint_id: str, job_id: str) -> dict:
        return await self._request("GET", f"{DATA_BASE}/{endpoint_id}/stream/{job_id}")

    async def status(self, endpoint_id: str, job_id: str) -> dict:
        return await self._request("GET", f"{DATA_BASE}/{endpoint_id}/status/{job_id}")

    async def cancel(self, endpoint_id: str, job_id: str) -> dict | None:
        return await self._request("POST", f"{DATA_BASE}/{endpoint_id}/cancel/{job_id}")


_client: RunPodClient | None = None


def get_client() -> RunPodClient:
    global _client
    if _client is None:
        _client = RunPodClient()
    return _client
