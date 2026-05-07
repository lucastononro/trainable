"""Unit tests for the per-provider auth resolver.

Only Claude has a non-API-key path today (CLAUDE_CODE_OAUTH_TOKEN for the
subscription flow, served via claude-agent-sdk). OpenAI / Gemini / LiteLLM
are env-var-only — the older Codex CLI / Gemini CLI OAuth fallback was
removed because nobody was using it.
"""

from __future__ import annotations

import pytest


def _clear_env(monkeypatch, *names: str):
    for name in names:
        monkeypatch.delenv(name, raising=False)


class TestClaude:
    def test_oauth_token_wins(self, monkeypatch):
        from services.llm.auth import claude as claude_auth

        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "ct-token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ignored")
        c = claude_auth.resolve()
        assert c.mode == "oauth_cli"
        assert c.token == "ct-token"
        assert c.transport == "claude_sdk"

    def test_api_key_fallback(self, monkeypatch):
        from services.llm.auth import claude as claude_auth

        _clear_env(monkeypatch, "CLAUDE_CODE_OAUTH_TOKEN")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        c = claude_auth.resolve()
        assert c.mode == "api_key"
        assert c.token == "sk-test"

    def test_neither_raises(self, monkeypatch):
        from services.llm.auth import claude as claude_auth
        from services.llm.auth._base import ProviderUnavailable

        _clear_env(monkeypatch, "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
        with pytest.raises(ProviderUnavailable):
            claude_auth.resolve()


class TestOpenAI:
    def test_api_key(self, monkeypatch):
        from services.llm.auth import openai as openai_auth

        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        c = openai_auth.resolve()
        assert c.mode == "api_key"
        assert c.token == "sk-openai"
        assert c.transport == "openai_sdk"

    def test_base_url_carried_through(self, monkeypatch):
        from services.llm.auth import openai as openai_auth

        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
        c = openai_auth.resolve()
        assert c.extra.get("base_url") == "https://example.com/v1"

    def test_unavailable(self, monkeypatch):
        from services.llm.auth import openai as openai_auth
        from services.llm.auth._base import ProviderUnavailable

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ProviderUnavailable):
            openai_auth.resolve()


class TestGemini:
    def test_gemini_env_var(self, monkeypatch):
        from services.llm.auth import gemini as gemini_auth

        monkeypatch.setenv("GEMINI_API_KEY", "g-test")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        c = gemini_auth.resolve()
        assert c.mode == "api_key"
        assert c.token == "g-test"
        assert c.transport == "gemini_sdk"

    def test_google_env_var_fallback(self, monkeypatch):
        from services.llm.auth import gemini as gemini_auth

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        c = gemini_auth.resolve()
        assert c.mode == "api_key"
        assert c.token == "g-test"

    def test_unavailable(self, monkeypatch):
        from services.llm.auth import gemini as gemini_auth
        from services.llm.auth._base import ProviderUnavailable

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ProviderUnavailable):
            gemini_auth.resolve()


class TestLiteLLM:
    def test_at_least_one_key(self, monkeypatch):
        from services.llm.auth import litellm as litellm_auth

        for v in litellm_auth._HINT_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
        c = litellm_auth.resolve()
        assert c.mode == "api_key"
        assert c.transport == "litellm_sdk"

    def test_unavailable_when_no_keys(self, monkeypatch):
        from services.llm.auth import litellm as litellm_auth
        from services.llm.auth._base import ProviderUnavailable

        for v in litellm_auth._HINT_VARS:
            monkeypatch.delenv(v, raising=False)
        with pytest.raises(ProviderUnavailable):
            litellm_auth.resolve()


class TestDispatch:
    def test_unknown_provider_id(self):
        from services.llm.auth import resolve_credentials
        from services.llm.auth._base import ProviderUnavailable

        with pytest.raises(ProviderUnavailable):
            resolve_credentials("not-a-provider")

    def test_aliases(self, monkeypatch):
        from services.llm.auth import resolve_credentials

        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "ct")
        c1 = resolve_credentials("claude")
        c2 = resolve_credentials("anthropic")
        assert c1.transport == c2.transport == "claude_sdk"
