"""Gemini credentials: GEMINI_API_KEY (or GOOGLE_API_KEY)."""

from __future__ import annotations

import os

from ._base import Credentials, ProviderUnavailable


def resolve() -> Credentials:
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise ProviderUnavailable(
            "Gemini unavailable: set GEMINI_API_KEY (or GOOGLE_API_KEY)."
        )
    return Credentials(
        mode="api_key",
        token=api_key,
        transport="gemini_sdk",
        extra={"env_var": "GEMINI_API_KEY"},
    )
