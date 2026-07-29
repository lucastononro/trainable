"""Compute-provider factory — the single switch on settings.compute_provider.

Every call site that needs provider behavior goes through one of the four
getters below; nothing outside services/compute imports a provider module
directly. Providers are lazy singletons so importing this package never
touches the network.
"""

from __future__ import annotations

import logging

from config import settings
from services.compute.base import (
    DeployResult,
    FileEntry,
    FileEntryType,
    KernelTransport,
    SandboxHandle,
    SandboxProvider,
    ServingBackend,
    StorageBackend,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DeployResult",
    "FileEntry",
    "FileEntryType",
    "KernelTransport",
    "SandboxHandle",
    "SandboxProvider",
    "ServingBackend",
    "StorageBackend",
    "get_kernel_transport_factory",
    "get_sandbox_provider",
    "get_serving_backend",
    "get_storage",
]

SUPPORTED_PROVIDERS = ("modal", "runpod")

_sandbox_provider: SandboxProvider | None = None
_storage: StorageBackend | None = None
_serving: ServingBackend | None = None


def _provider() -> str:
    p = (settings.compute_provider or "modal").lower()
    if p not in SUPPORTED_PROVIDERS:
        raise RuntimeError(
            f"COMPUTE_PROVIDER={p!r} is not supported. "
            f"Pick one of: {', '.join(SUPPORTED_PROVIDERS)}."
        )
    if p == "runpod":
        _validate_runpod_settings()
    return p


def _validate_runpod_settings() -> None:
    missing = [
        env
        for env, value in (
            ("RUNPOD_API_KEY", settings.runpod_api_key),
            ("RUNPOD_S3_ACCESS_KEY_ID", settings.runpod_s3_access_key_id),
            ("RUNPOD_S3_SECRET_ACCESS_KEY", settings.runpod_s3_secret_access_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "COMPUTE_PROVIDER=runpod but required settings are missing: "
            + ", ".join(missing)
            + ". Create an API key and an S3 API key in the RunPod console "
            "(Settings → API Keys / S3 API Keys) and add them to .env."
        )


def get_sandbox_provider() -> SandboxProvider:
    global _sandbox_provider
    provider = _provider()
    if _sandbox_provider is None or _sandbox_provider.name != provider:
        if provider == "runpod":
            from services.compute.runpod_provider.sandbox import RunPodSandboxProvider

            _sandbox_provider = RunPodSandboxProvider()
        else:
            from services.compute.modal_provider.sandbox import ModalSandboxProvider

            _sandbox_provider = ModalSandboxProvider()
    return _sandbox_provider


def get_kernel_transport_factory():
    """Return an async callable `(session_id: str) -> KernelTransport`."""
    provider = _provider()
    if provider == "runpod":
        from services.compute.runpod_provider.kernel import (
            create_runpod_kernel_transport,
        )

        return create_runpod_kernel_transport
    from services.compute.modal_provider.kernel import create_modal_kernel_transport

    return create_modal_kernel_transport


def get_storage() -> StorageBackend:
    global _storage
    provider = _provider()
    if _storage is None or getattr(_storage, "name", None) != provider:
        if provider == "runpod":
            from services.compute.runpod_provider.storage import RunPodStorage

            _storage = RunPodStorage()
        else:
            from services.compute.modal_provider.storage import ModalStorage

            _storage = ModalStorage()
    return _storage


def get_serving_backend() -> ServingBackend:
    global _serving
    provider = _provider()
    if _serving is None or _serving.name != provider:
        if provider == "runpod":
            from services.compute.runpod_provider.serving import RunPodServingBackend

            _serving = RunPodServingBackend()
        else:
            from services.compute.modal_provider.serving import ModalServingBackend

            _serving = ModalServingBackend()
    return _serving


def kernel_ready_timeout_s() -> int:
    """Provider-aware kernel readiness timeout. RunPod pods can spend
    minutes pulling the worker image on a fresh machine; Modal sandboxes
    boot in seconds."""
    return 600 if _provider() == "runpod" else 120
