"""Unit tests for the per-provider auth resolver.

The resolver picks between OAuth-CLI mode (a credentials file under the user's
home dir + the CLI binary on PATH) and API-key mode (env var only). These
tests pin the contract so prod/dev divergence stays predictable.
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
    def test_codex_oauth_when_file_and_cli_present(self, monkeypatch):
        from services.llm.auth import openai as openai_auth

        # Pretend the auth file is there and `codex` resolves on PATH.
        monkeypatch.setattr(
            openai_auth,
            "_codex_auth_file",
            lambda: openai_auth.Path("/home/u/.codex/auth.json"),
        )
        monkeypatch.setattr(
            openai_auth.shutil,
            "which",
            lambda b: "/usr/bin/codex" if b == "codex" else None,
        )
        c = openai_auth.resolve()
        assert c.mode == "oauth_cli"
        assert c.transport == "codex_cli"

    def test_api_key_when_file_missing(self, monkeypatch):
        from services.llm.auth import openai as openai_auth

        monkeypatch.setattr(openai_auth, "_codex_auth_file", lambda: None)
        monkeypatch.setattr(openai_auth.shutil, "which", lambda b: None)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        c = openai_auth.resolve()
        assert c.mode == "api_key"
        assert c.token == "sk-openai"
        assert c.transport == "openai_sdk"

    def test_api_key_when_cli_missing_even_if_file_present(self, monkeypatch):
        from services.llm.auth import openai as openai_auth

        monkeypatch.setattr(
            openai_auth,
            "_codex_auth_file",
            lambda: openai_auth.Path("/somewhere/auth.json"),
        )
        monkeypatch.setattr(openai_auth.shutil, "which", lambda b: None)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fallback")
        c = openai_auth.resolve()
        assert c.mode == "api_key"

    def test_unavailable(self, monkeypatch):
        from services.llm.auth import openai as openai_auth
        from services.llm.auth._base import ProviderUnavailable

        monkeypatch.setattr(openai_auth, "_codex_auth_file", lambda: None)
        monkeypatch.setattr(openai_auth.shutil, "which", lambda b: None)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ProviderUnavailable):
            openai_auth.resolve()


class TestGemini:
    def test_oauth_when_file_and_cli_present(self, monkeypatch):
        from services.llm.auth import gemini as gemini_auth

        monkeypatch.setattr(
            gemini_auth,
            "_gemini_auth_file",
            lambda: gemini_auth.Path("/home/u/.gemini/oauth_creds.json"),
        )
        monkeypatch.setattr(
            gemini_auth.shutil,
            "which",
            lambda b: "/usr/bin/gemini" if b == "gemini" else None,
        )
        c = gemini_auth.resolve()
        assert c.mode == "oauth_cli"
        assert c.transport == "gemini_cli"

    def test_gemini_or_google_env_var(self, monkeypatch):
        from services.llm.auth import gemini as gemini_auth

        monkeypatch.setattr(gemini_auth, "_gemini_auth_file", lambda: None)
        monkeypatch.setattr(gemini_auth.shutil, "which", lambda b: None)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        c = gemini_auth.resolve()
        assert c.mode == "api_key"
        assert c.token == "g-test"


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
