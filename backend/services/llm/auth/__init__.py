"""Auth resolution per LLM provider.

Each sub-module exposes a `resolve() -> Credentials`. The factory calls
`resolve_credentials(provider_id)` once per provider during bootstrap; the
result is stored on the provider instance and reflected in
`ProviderCapabilities.auth_mode` for telemetry.

Resolution preference is consistent across providers:
  1. OAuth file present (local dev with mounted ~/.codex, ~/.gemini, ~/.claude)
     -> CLI subprocess transport
  2. Env var set (production / API-key path)
     -> SDK transport
  3. Neither -> ProviderUnavailable, factory skips registration
"""

from __future__ import annotations

from . import claude, gemini, litellm, openai
from ._base import Credentials, ProviderUnavailable

_RESOLVERS = {
    "claude": claude.resolve,
    "anthropic": claude.resolve,
    "openai": openai.resolve,
    "gemini": gemini.resolve,
    "google": gemini.resolve,
    "litellm": litellm.resolve,
}


def resolve_credentials(provider_id: str) -> Credentials:
    """Resolve credentials for `provider_id`. Raises ProviderUnavailable."""
    if provider_id not in _RESOLVERS:
        raise ProviderUnavailable(f"No auth resolver for provider '{provider_id}'.")
    return _RESOLVERS[provider_id]()


__all__ = ["Credentials", "ProviderUnavailable", "resolve_credentials"]
