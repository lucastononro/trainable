"""Auth resolution primitives shared across providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


AuthMode = Literal["api_key", "oauth_cli"]


@dataclass
class Credentials:
    """Resolved credentials for one LLM provider.

    `mode` is the authentication style:
      - "api_key": the SDK is initialized with `token`
      - "oauth_cli": a local CLI subprocess (Claude CLI, Codex CLI, Gemini CLI)
        is invoked which reads its own OAuth state from the user's home dir.

    `transport` names the concrete transport to spawn for `oauth_cli` mode
    (e.g. "claude_sdk", "codex_cli", "gemini_cli"). Ignored for `api_key` mode.

    `auth_file` is the OAuth state file path that signaled the choice (purely
    informational — for telemetry / `trainable doctor`).

    `extra` carries provider-specific bits (e.g. base_url for OpenAI-compatible
    deployments, organization id, etc.).
    """

    mode: AuthMode
    token: str = ""
    transport: str = ""
    auth_file: str = ""
    extra: dict = field(default_factory=dict)


class ProviderUnavailable(RuntimeError):
    """Raised when no usable credentials can be resolved for a provider.

    The factory catches this to skip registering the provider entirely so
    `list_providers()` reflects what the runtime can actually call.
    """
