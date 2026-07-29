"""Tests for secrets-file permission handling (#127, PR #134).

The generated ~/.trainable/.env holds plaintext API keys, so it must:
  * be created at 0600 from the very first instant (no TOCTOU window where
    a umask-derived 0644 file exists before a later chmod), and
  * be tightened back to 0600 when a pre-existing looser file is rewritten.
"""

from __future__ import annotations

import os
import stat

import pytest

from trainable_cli.main import ENV_FILE, read_env, write_env

needs_posix = pytest.mark.skipif(
    os.name != "posix", reason="POSIX file permissions required"
)

SAMPLE_CONFIG = {
    "ANTHROPIC_API_KEY": "sk-ant-test-123",
    "MODAL_TOKEN_ID": "ak-test",
    "MODAL_TOKEN_SECRET": "as-test",
}


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def permissive_umask():
    """Force the loosest possible umask so any permission narrowing we
    observe must come from the code under test, not the environment."""
    old = os.umask(0)
    try:
        yield
    finally:
        os.umask(old)


@needs_posix
def test_fresh_env_file_created_0600(tmp_path, permissive_umask):
    write_env(tmp_path, SAMPLE_CONFIG)
    assert _mode(tmp_path / ENV_FILE) == 0o600


@needs_posix
def test_no_toctou_window_perms_come_from_creation(tmp_path, permissive_umask, monkeypatch):
    """The file must be born 0600 — not created loose and chmod'ed after.

    With os.chmod neutered, the only way the file can end up 0600 is if the
    O_CREAT mode itself was 0600, i.e. there was never a window where the
    file existed with looser permissions.
    """
    monkeypatch.setattr(os, "chmod", lambda *a, **kw: None)
    write_env(tmp_path, SAMPLE_CONFIG)
    assert _mode(tmp_path / ENV_FILE) == 0o600


@needs_posix
def test_preexisting_loose_env_tightened_on_rewrite(tmp_path, permissive_umask):
    env_path = tmp_path / ENV_FILE
    env_path.write_text("OLD=1\n")
    os.chmod(env_path, 0o644)
    assert _mode(env_path) == 0o644  # sanity: starts loose

    write_env(tmp_path, SAMPLE_CONFIG)
    assert _mode(env_path) == 0o600


def test_rewrite_truncates_previous_content(tmp_path):
    """O_TRUNC: a shorter rewrite must not leave stale bytes behind."""
    long_config = dict(SAMPLE_CONFIG, EXTRA_BACKEND_API_KEY="x" * 500)
    write_env(tmp_path, long_config)
    write_env(tmp_path, SAMPLE_CONFIG)
    text = (tmp_path / ENV_FILE).read_text()
    assert "EXTRA_BACKEND_API_KEY" not in text
    assert text.endswith("\n")


def test_content_roundtrip_through_read_env(tmp_path):
    write_env(tmp_path, SAMPLE_CONFIG)
    parsed = read_env(tmp_path)
    for key, value in SAMPLE_CONFIG.items():
        assert parsed[key] == value
