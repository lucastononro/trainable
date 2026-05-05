"""LLM provider abstraction.

This package wraps the various LLM SDKs behind a single Protocol so the
agent runner doesn't need to know which provider it's talking to.

Today the Claude provider is the production path — it wraps
`claude-agent-sdk` with full sub-agent + MCP support. The OpenAI and Gemini
providers offer a simpler tool-using chat loop suitable for single-agent
workloads (no sub-agent delegation yet).
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
