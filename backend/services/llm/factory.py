"""Factory — resolve a provider id to a LLMProvider instance."""

from __future__ import annotations

import logging
from typing import Callable

from .base import LLMProvider

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable[[], LLMProvider]] = {}
_INSTANCES: dict[str, LLMProvider] = {}


def register_provider(provider_id: str, factory: Callable[[], LLMProvider]) -> None:
    """Register a provider lazily — the factory is called on first use."""
    _REGISTRY[provider_id] = factory


def get_provider(provider_id: str) -> LLMProvider:
    """Return the provider for `provider_id`. Raises KeyError if unknown."""
    if provider_id not in _INSTANCES:
        if provider_id not in _REGISTRY:
            available = ", ".join(sorted(_REGISTRY.keys())) or "(none registered)"
            raise KeyError(
                f"Unknown LLM provider '{provider_id}'. Available: {available}"
            )
        _INSTANCES[provider_id] = _REGISTRY[provider_id]()
    return _INSTANCES[provider_id]


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def _bootstrap():
    """Lazy-import providers and register them. Imports are inside this
    function so the module loads even when optional SDKs aren't installed.
    """
    try:
        from .claude_provider import ClaudeProvider

        register_provider("claude", lambda: ClaudeProvider())
        register_provider("anthropic", lambda: ClaudeProvider())
    except Exception as e:
        logger.warning("ClaudeProvider unavailable: %s", e)

    try:
        from .openai_provider import OpenAIProvider

        register_provider("openai", lambda: OpenAIProvider())
    except Exception as e:
        logger.debug("OpenAIProvider not registered: %s", e)

    try:
        from .gemini_provider import GeminiProvider

        register_provider("gemini", lambda: GeminiProvider())
        register_provider("google", lambda: GeminiProvider())
    except Exception as e:
        logger.debug("GeminiProvider not registered: %s", e)


_bootstrap()
