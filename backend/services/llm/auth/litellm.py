"""LiteLLM credentials: presence-only check.

LiteLLM dispatches to dozens of backends, each with its own API-key env var
(GROQ_API_KEY, MISTRAL_API_KEY, TOGETHER_API_KEY, etc.). We don't gate the
provider on any single key — at least one must be set, validated lazily when
the user actually calls a model whose backend needs it.

The token field stays empty; the provider just checks `litellm` is importable
and trusts that the user has configured whichever backend they want.
"""

from __future__ import annotations

import os

from ._base import Credentials, ProviderUnavailable


# Loose check: if any of these env vars exist, assume the user wired LiteLLM.
# Not exhaustive — LiteLLM supports >100 backends. The provider itself raises
# a clearer error at call time when a specific backend's key is missing.
_HINT_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "TOGETHER_API_KEY",
    "DEEPSEEK_API_KEY",
    "COHERE_API_KEY",
    "REPLICATE_API_TOKEN",
    "OPENROUTER_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AZURE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


def resolve() -> Credentials:
    if not any(os.getenv(k) for k in _HINT_VARS):
        raise ProviderUnavailable(
            "LiteLLM has no detectable backend credentials — set at least one of: "
            + ", ".join(_HINT_VARS[:5])
            + ", … (see LiteLLM docs)."
        )
    return Credentials(mode="api_key", token="", transport="litellm_sdk")
