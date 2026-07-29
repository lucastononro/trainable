"""Tests for CORS_ORIGINS parsing/validation in config.Settings.

Regression coverage for the Greptile findings on PR #140:
- mixing '*' with explicit origins must fail fast (not silently strip
  credentials from the explicit entries),
- the pre-NoDecode JSON-array env format must parse correctly instead of
  being comma-split into a garbled bracketed origin.
"""

import pytest
from pydantic import ValidationError

from config import Settings


def make_settings(**kwargs) -> Settings:
    """Settings isolated from any local .env file."""
    return Settings(_env_file=None, **kwargs)


# -- comma-separated strings (the documented env format) --


def test_csv_string_is_split():
    s = make_settings(cors_origins="https://app.example.com,http://localhost:3000")
    assert s.cors_origins == ["https://app.example.com", "http://localhost:3000"]


def test_csv_string_strips_whitespace_and_empty_entries():
    s = make_settings(cors_origins=" https://a.example , ,http://b.example ,")
    assert s.cors_origins == ["https://a.example", "http://b.example"]


def test_single_origin_string():
    s = make_settings(cors_origins="https://app.example.com")
    assert s.cors_origins == ["https://app.example.com"]


def test_real_list_passes_through():
    s = make_settings(cors_origins=["https://app.example.com"])
    assert s.cors_origins == ["https://app.example.com"]


def test_default_is_local_frontend():
    s = make_settings()
    assert s.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


# -- JSON-array env format (pre-NoDecode deployments) --


def test_json_array_string_is_parsed_not_comma_split():
    s = make_settings(
        cors_origins='["http://localhost:3000", "https://app.example.com"]'
    )
    assert s.cors_origins == ["http://localhost:3000", "https://app.example.com"]


def test_json_array_single_entry():
    s = make_settings(cors_origins='["http://localhost:3000"]')
    assert s.cors_origins == ["http://localhost:3000"]


def test_malformed_bracketed_value_raises_clear_error():
    with pytest.raises(ValidationError, match="not valid.*JSON"):
        make_settings(cors_origins="[http://localhost:3000]")


def test_json_array_of_non_strings_raises():
    with pytest.raises(ValidationError, match="array of strings"):
        make_settings(cors_origins="[1, 2]")


# -- wildcard rules --


def test_wildcard_alone_is_allowed():
    s = make_settings(cors_origins="*")
    assert s.cors_origins == ["*"]


def test_wildcard_mixed_with_explicit_origin_raises():
    with pytest.raises(ValidationError, match="cannot mix '\\*'"):
        make_settings(cors_origins="*,http://localhost:3000")


def test_wildcard_mixed_in_list_raises():
    with pytest.raises(ValidationError, match="cannot mix '\\*'"):
        make_settings(cors_origins=["*", "http://localhost:3000"])


def test_wildcard_mixed_in_json_array_raises():
    with pytest.raises(ValidationError, match="cannot mix '\\*'"):
        make_settings(cors_origins='["*", "http://localhost:3000"]')


# -- env-var path end to end --


def test_env_var_csv(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://x.example,https://y.example")
    s = make_settings()
    assert s.cors_origins == ["https://x.example", "https://y.example"]


def test_env_var_json_array(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["https://x.example"]')
    s = make_settings()
    assert s.cors_origins == ["https://x.example"]


def test_env_var_wildcard_plus_explicit_raises(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*,https://x.example")
    with pytest.raises(ValidationError, match="cannot mix '\\*'"):
        make_settings()
