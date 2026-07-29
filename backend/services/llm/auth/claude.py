"""Claude credentials: prefer Claude Code OAuth token, fall back to API key.

The claude-agent-sdk subprocesses to the `claude` CLI, which itself accepts
either CLAUDE_CODE_OAUTH_TOKEN (subscription) or ANTHROPIC_API_KEY. We mirror
that priority here so telemetry reflects the real auth path.
"""

from __future__ import annotations

import os

from ._base import Credentials, ProviderUnavailable


def resolve() -> Credentials:
    oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if oauth_token:
        return Credentials(
            mode="oauth_cli",
            token=oauth_token,
            transport="claude_sdk",
            extra={"env_var": "CLAUDE_CODE_OAUTH_TOKEN"},
        )
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        return Credentials(
            mode="api_key",
            token=api_key,
            transport="claude_sdk",
            extra={"env_var": "ANTHROPIC_API_KEY"},
        )
    raise ProviderUnavailable(
        "Claude unavailable: set CLAUDE_CODE_OAUTH_TOKEN (subscription) or "
        "ANTHROPIC_API_KEY."
    )
