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
      - "oauth_cli": a Claude Code OAuth token (used by claude-agent-sdk
        when CLAUDE_CODE_OAUTH_TOKEN is present). Only Claude uses this
        mode today; OpenAI and Gemini are api_key only.

    `transport` names the concrete SDK / wrapper used at call time
    (e.g. "claude_sdk", "openai_sdk", "gemini_sdk", "litellm_sdk").

    `auth_file` is left for backwards-compat / telemetry — empty in
    practice now that no provider reads from a home-dir OAuth file.

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
