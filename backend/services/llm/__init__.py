"""LLM provider abstraction.

This package wraps the various LLM SDKs behind a single Protocol so the
agent runner doesn't need to know which provider it's talking to.

All providers participate in the same multi-agent runtime. Sub-agent
delegation is a runner-level concern, not a provider-level one: the
`delegate-task` skill is dispatched as a regular tool by the runner's
non-Claude tool loop and recursively invokes `run_agent` for the child.
The Claude provider takes a slightly different path because the
`claude-agent-sdk` bakes its toolset/MCP server in upfront and runs the
multi-turn loop internally; OpenAI / Gemini / LiteLLM use the runner's
own per-turn dispatch. Both shapes support the same delegation graph.
"""

from .base import LLMEvent, LLMProvider, ProviderCapabilities
from .factory import get_provider, list_providers, register_provider

__all__ = [
    "LLMEvent",
    "LLMProvider",
    "ProviderCapabilities",
    "get_provider",
    "list_providers",
    "register_provider",
]
