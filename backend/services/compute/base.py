"""Compute-provider interfaces — the contract every provider implements.

Four concerns, four interfaces (they have different lifecycles):

  SandboxProvider   one-shot code execution with streamed stdout/stderr
  KernelTransport   a long-lived newline-JSON command/event channel for
                    the notebook kernel proxy
  StorageBackend    the shared workspace filesystem, mirrored 1:1 from the
                    helpers in services/volume.py
  ServingBackend    turn a registered model into a live HTTP endpoint

The Modal implementations live in services/compute/modal_provider/ and the
RunPod ones in services/compute/runpod_provider/. Call sites never import
providers directly — they go through the factory in services/compute/__init__.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Sandbox execution
# ---------------------------------------------------------------------------


class SandboxTimeoutError(TimeoutError):
    """The provider killed the sandbox at its configured timeout.

    Modal raises its own modal.exception.SandboxTimeoutError; RunPod jobs
    end with status TIMED_OUT and the handle raises this. Handlers that
    care about timeouts catch both.
    """


@runtime_checkable
class SandboxHandle(Protocol):
    """A running one-shot sandbox. `stdout`/`stderr` are async iterators of
    text chunks; `returncode` is valid after `wait()` returns."""

    stdout: AsyncIterator[str]
    stderr: AsyncIterator[str]
    returncode: int | None

    async def wait(self) -> int | None: ...

    async def terminate(self) -> None: ...


class SandboxProvider(ABC):
    """Creates one-shot sandboxes that run `python -u -c <code>`."""

    #: provider key used for billing rows and rate lookup (sandbox.yml)
    name: str

    @abstractmethod
    async def create(
        self,
        *,
        code: str,
        session_id: str,
        gpu: str | None,
        timeout: int,
        workdir: str,
    ) -> SandboxHandle: ...


# ---------------------------------------------------------------------------
# Notebook kernel transport
# ---------------------------------------------------------------------------


@runtime_checkable
class KernelTransport(Protocol):
    """Bidirectional newline-JSON channel to the in-sandbox kernel proxy.

    `send()` delivers one JSON command line; `events()` yields JSON event
    lines emitted by the proxy (without trailing newline). `terminate()`
    tears the underlying sandbox/pod down.
    """

    async def send(self, line: str) -> None: ...

    def events(self) -> AsyncIterator[str]: ...

    async def terminate(self) -> None: ...


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class FileEntryType(Enum):
    """Mirror of modal.volume.FileEntryType's shape — consumers only ever
    compare `entry.type.name` against "FILE"."""

    FILE = 1
    DIRECTORY = 2
    SYMLINK = 3


@dataclass
class FileEntry:
    """Normalized listing entry. Modal's own FileEntry duck-types this
    (it has `.path` and `.type.name`), so the modal backend returns Modal
    entries untouched and only RunPod synthesizes these."""

    path: str
    type: FileEntryType
    size: int | None = None
    mtime: float | None = None


class StorageBackend(ABC):
    """The shared workspace volume, addressed by volume-relative paths
    (leading slash, no /data prefix) — e.g. `/sessions/{sid}/data/x.csv`."""

    @abstractmethod
    async def read_file(self, path: str) -> bytes: ...

    @abstractmethod
    async def listdir(self, path: str, recursive: bool = False) -> list: ...

    @abstractmethod
    async def upload(self, local_path: str, remote_path: str) -> None: ...

    @abstractmethod
    async def upload_many(self, pairs: list[tuple[str, str]]) -> int: ...

    @abstractmethod
    async def remove(self, path: str) -> None: ...

    @abstractmethod
    async def write(self, content: str | bytes, remote_path: str) -> None: ...

    @abstractmethod
    async def ensure_session_workspace(self, session_id: str) -> None: ...

    @abstractmethod
    async def reload(self) -> bool:
        """Refresh the backend's view of sandbox writes. No-op (True) for
        backends whose reads are always live (S3-backed)."""

    def reload_sync(self) -> bool:
        """Sync variant for the rare non-async call sites. Default: no-op."""
        return True


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------


@dataclass
class DeployResult:
    """What a successful ServingBackend.deploy returns."""

    endpoint_url: str
    provider_app: str
    provider_function: str
    endpoint_id: str | None = None
    extra: dict[str, Any] | None = None


class ServingBackend(ABC):
    """Deploys a registered model's generated serving code as a live
    HTTP endpoint. The orchestration (DB rows, API-key generation,
    superseding old deployments) stays in services/deploy.py."""

    #: provider key ("modal" | "runpod")
    name: str

    #: filename of the generated serving module on the volume
    serving_filename: str

    @abstractmethod
    def app_name(self, project_id: str) -> str: ...

    @abstractmethod
    def fn_name(self, model_name: str, version: int) -> str: ...

    @abstractmethod
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
    ) -> str: ...

    @abstractmethod
    async def ensure_secret(self, model_id: str, api_key: str) -> str | None:
        """Make the per-model API key available to the serving container.
        Returns the secret's name/identifier, or None when the backend
        delivers the key another way (e.g. endpoint env vars)."""

    @abstractmethod
    async def deploy(
        self,
        *,
        model: Any,
        serving_app_path: str,
        compute: str,
        api_key: str | None,
    ) -> DeployResult: ...

    @abstractmethod
    async def stop(self, deployment_row: Any) -> None: ...

    @abstractmethod
    async def rotate_key(self, model_id: str, new_key: str) -> str | None:
        """Propagate a rotated API key; returns the secret name/identifier."""
