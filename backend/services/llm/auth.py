"""Per-provider auth resolution.

For each LLM provider, decide which transport to use based on which
credentials are present in the environment. Two modes today:

- ``oauth_cli`` — local-dev OAuth via the Claude Code CLI (subscription
  quota, no per-token billing). Active when ``CLAUDE_CODE_OAUTH_TOKEN``
  is set. Only available for Claude — OpenAI and Gemini have file-based
  CLI OAuth flows that aren't wired up yet.
- ``api_key`` — direct native-SDK calls. Used when an API key is set
  for the provider. Production-ready, billable.

Resolution order favors OAuth where it's available, since on local dev
that path is free against the user's subscription.

The runner uses this resolver to decide whether to take the
claude-agent-sdk shortcut (Claude OAuth) or to dispatch through
``factory.get_provider()`` (everything else, including Claude API key).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

AuthMode = Literal["oauth_cli", "api_key", "none"]


@dataclass(frozen=True)
class Credentials:
    """Resolved auth for one provider."""

    provider: str
    mode: AuthMode
    # Env var name whose value should be used as the credential. Empty
    # for `oauth_cli` (the SDK reads the file/token itself) and for
    # `none` (no auth resolved).
    env_var: str = ""
    # When mode is `none`, this is the list of env vars the user could
    # set to enable the provider — the picker UI surfaces it as a hint.
    missing_env: tuple[str, ...] = ()


# Env vars per provider, in resolution priority. First-set-wins within
# a mode (OAuth beats api_key when both can resolve).
_OAUTH_ENV: dict[str, tuple[str, ...]] = {
    "claude": ("CLAUDE_CODE_OAUTH_TOKEN",),
    "anthropic": ("CLAUDE_CODE_OAUTH_TOKEN",),
}

_API_KEY_ENV: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def _first_set(names: tuple[str, ...]) -> str:
    for n in names:
        if os.getenv(n):
            return n
    return ""


def resolve(provider_id: str) -> Credentials:
    """Resolve credentials for ``provider_id``.

    Returns ``mode="none"`` when neither an OAuth token nor any API key
    env var is set. Caller is expected to surface ``missing_env`` to the
    user (e.g., the picker UI gates on it).
    """
    pid = (provider_id or "").lower()

    oauth_names = _OAUTH_ENV.get(pid, ())
    if oauth_names:
        oauth_var = _first_set(oauth_names)
        if oauth_var:
            return Credentials(provider=pid, mode="oauth_cli", env_var=oauth_var)

    api_names = _API_KEY_ENV.get(pid, ())
    api_var = _first_set(api_names)
    if api_var:
        return Credentials(provider=pid, mode="api_key", env_var=api_var)

    # Nothing resolved — collect every env var the user could set.
    missing: list[str] = []
    seen: set[str] = set()
    for n in (*oauth_names, *api_names):
        if n not in seen:
            missing.append(n)
            seen.add(n)
    return Credentials(
        provider=pid,
        mode="none",
        env_var="",
        missing_env=tuple(missing),
    )


def is_available(provider_id: str) -> bool:
    """True when at least one credential is set for the provider."""
    return resolve(provider_id).mode != "none"
