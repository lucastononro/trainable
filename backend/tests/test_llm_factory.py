"""Unit tests for the LLM provider factory."""

from __future__ import annotations

import pytest


class _FakeProvider:
    capabilities = type("C", (), {"name": "fake", "supports_mcp": False})()

    async def run(self, **_):
        if False:
            yield None


@pytest.fixture
def empty_factory(monkeypatch):
    """Wipe the registry so each test starts clean."""
    from services.llm import factory
    monkeypatch.setattr(factory, "_REGISTRY", {})
    monkeypatch.setattr(factory, "_INSTANCES", {})
    yield factory


def test_register_and_get(empty_factory):
    empty_factory.register_provider("fake", lambda: _FakeProvider())
    assert empty_factory.list_providers() == ["fake"]
    inst = empty_factory.get_provider("fake")
    assert isinstance(inst, _FakeProvider)


def test_get_caches_instance(empty_factory):
    counter = {"n": 0}

    def factory_fn():
        counter["n"] += 1
        return _FakeProvider()

    empty_factory.register_provider("fake", factory_fn)
    a = empty_factory.get_provider("fake")
    b = empty_factory.get_provider("fake")
    assert a is b
    assert counter["n"] == 1


def test_unknown_provider_raises(empty_factory):
    with pytest.raises(KeyError, match="Unknown LLM provider"):
        empty_factory.get_provider("nope")


def test_bootstrap_registers_known_ids():
    """The real bootstrap registers each provider that imports cleanly.

    In CI / dev environments where claude_agent_sdk or google-genai aren't
    installed, those providers silently skip registration. The two that have
    no SDK gate at registration time (openai, litellm) must always appear.
    """
    from services.llm import factory
    factory._bootstrap()
    ids = set(factory.list_providers())
    assert "openai" in ids
    assert "litellm" in ids
    # Claude registers under both `claude` and `anthropic` when available.
    if "claude" in ids:
        assert "anthropic" in ids
    # Gemini registers under both `gemini` and `google` when available.
    if "gemini" in ids:
        assert "google" in ids


def test_factory_failure_does_not_break_bootstrap(monkeypatch):
    """If one provider's import raises, the others still register."""
    from services.llm import factory
    monkeypatch.setattr(factory, "_REGISTRY", {})
    monkeypatch.setattr(factory, "_INSTANCES", {})

    # Force ClaudeProvider import to explode by clobbering the module path.
    import sys
    saved = sys.modules.pop("services.llm.claude_provider", None)
    monkeypatch.setitem(sys.modules, "services.llm.claude_provider", None)
    try:
        factory._bootstrap()
        # The other three providers should still bootstrap.
        ids = set(factory.list_providers())
        assert "openai" in ids
        assert "gemini" in ids
        assert "litellm" in ids
    finally:
        if saved is not None:
            sys.modules["services.llm.claude_provider"] = saved
        else:
            sys.modules.pop("services.llm.claude_provider", None)
