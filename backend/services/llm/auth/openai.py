"""OpenAI credentials: OPENAI_API_KEY (with optional OPENAI_BASE_URL)."""

from __future__ import annotations

import os

from ._base import Credentials, ProviderUnavailable


def resolve() -> Credentials:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ProviderUnavailable("OpenAI unavailable: set OPENAI_API_KEY.")
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    return Credentials(
        mode="api_key",
        token=api_key,
        transport="openai_sdk",
        extra={"base_url": base_url} if base_url else {},
    )
