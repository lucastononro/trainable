"""OpenAI credentials: API key, or Codex CLI OAuth (~/.codex/auth.json).

Local-dev preference: when the user mounts ~/.codex into the container AND has
the `codex` binary on PATH, we use the OAuth-backed Codex CLI subprocess so
ChatGPT-Plus subscribers don't burn API credits. In production (no mount, no
binary), we fall through to the OpenAI SDK with OPENAI_API_KEY.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ._base import Credentials, ProviderUnavailable


def _codex_auth_file() -> Path | None:
    """Return the Codex CLI's auth file if it exists, else None."""
    home = Path(os.path.expanduser("~"))
    candidate = home / ".codex" / "auth.json"
    return candidate if candidate.exists() else None


def resolve() -> Credentials:
    auth_file = _codex_auth_file()
    cli_present = shutil.which("codex") is not None

    # OAuth path is only viable if BOTH the auth file exists and the CLI is
    # callable. In a deployed container neither is true and we fall through.
    if auth_file is not None and cli_present:
        return Credentials(
            mode="oauth_cli",
            token="",
            transport="codex_cli",
            auth_file=str(auth_file),
        )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        base_url = os.getenv("OPENAI_BASE_URL", "").strip()
        return Credentials(
            mode="api_key",
            token=api_key,
            transport="openai_sdk",
            extra={"base_url": base_url} if base_url else {},
        )

    raise ProviderUnavailable(
        "OpenAI unavailable: set OPENAI_API_KEY, or `npm install -g @openai/codex && "
        "codex login` (then mount ~/.codex into the container) for ChatGPT OAuth."
    )
