"""Gemini credentials: API key, or Gemini CLI OAuth (~/.gemini/oauth_creds.json).

Same pattern as OpenAI: local-dev OAuth via the Gemini CLI subprocess; prod
falls back to GEMINI_API_KEY (or GOOGLE_API_KEY) with the genai SDK.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ._base import Credentials, ProviderUnavailable


def _gemini_auth_file() -> Path | None:
    home = Path(os.path.expanduser("~"))
    for candidate in (
        home / ".gemini" / "oauth_creds.json",
        home / ".gemini" / "credentials.json",
    ):
        if candidate.exists():
            return candidate
    return None


def resolve() -> Credentials:
    auth_file = _gemini_auth_file()
    cli_present = shutil.which("gemini") is not None

    if auth_file is not None and cli_present:
        return Credentials(
            mode="oauth_cli",
            token="",
            transport="gemini_cli",
            auth_file=str(auth_file),
        )

    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if api_key:
        return Credentials(
            mode="api_key",
            token=api_key,
            transport="gemini_sdk",
            extra={"env_var": "GEMINI_API_KEY"},
        )

    raise ProviderUnavailable(
        "Gemini unavailable: set GEMINI_API_KEY (or GOOGLE_API_KEY), or install "
        "the Gemini CLI and run `gemini auth login` for OAuth."
    )
