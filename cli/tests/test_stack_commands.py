"""Tests for the Docker preflight and stack-introspection commands (#126).

`cmd_up`/`cmd_down` used to exec straight into `docker compose`, so a
stopped Docker daemon surfaced a raw connection error. Now `_require_config`
runs a `docker info` liveness preflight, and the CLI grows `status`, `logs`,
and `--version`. All docker subprocesses are mocked — no real Docker needed.
"""

from __future__ import annotations

import subprocess

import pytest

import trainable_cli.main as cli_main
from trainable_cli.main import (
    COMPOSE_FILE,
    ENV_FILE,
    cmd_down,
    cmd_logs,
    cmd_status,
    cmd_up,
    main,
)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Point the CLI at a tmp config dir holding a compose file and .env,
    with a fake docker binary on PATH and no browser-opener fork."""
    (tmp_path / COMPOSE_FILE).write_text("services: {}\n")
    (tmp_path / ENV_FILE).write_text("ANTHROPIC_API_KEY=sk-ant-test\n")
    monkeypatch.setattr(cli_main, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli_main.shutil, "which", lambda cmd: "/usr/bin/docker")
    monkeypatch.setenv("TRAINABLE_NO_BROWSER", "1")
    return tmp_path


@pytest.fixture
def _docker_ok(monkeypatch):
    """Pretend `docker info` succeeds."""
    monkeypatch.setattr(
        cli_main.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""),
    )


@pytest.fixture
def _docker_daemon_down(monkeypatch):
    """Pretend `docker info` fails the way it does when the daemon is stopped."""
    monkeypatch.setattr(
        cli_main.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 1, "", "Cannot connect to the Docker daemon"
        ),
    )


@pytest.fixture
def captured_exec(monkeypatch):
    """Neuter os.execvp (it replaces the process) and capture its args."""
    calls = []
    monkeypatch.setattr(cli_main.os, "execvp", lambda *a: calls.append(a))
    return calls


def test_up_fails_friendly_when_daemon_down(
    config_dir, _docker_daemon_down, captured_exec, capsys
):
    with pytest.raises(SystemExit) as exc:
        cmd_up()
    assert exc.value.code == 1
    assert captured_exec == []  # never reaches docker compose
    assert "daemon is not running" in capsys.readouterr().out


def test_down_fails_friendly_when_daemon_down(
    config_dir, _docker_daemon_down, captured_exec
):
    with pytest.raises(SystemExit) as exc:
        cmd_down()
    assert exc.value.code == 1
    assert captured_exec == []


def test_up_execs_compose_up_when_daemon_alive(config_dir, _docker_ok, captured_exec):
    cmd_up()
    assert len(captured_exec) == 1
    _, args = captured_exec[0]
    assert args[:2] == ["docker", "compose"]
    assert args[-1] == "up"


def test_status_execs_compose_ps(config_dir, _docker_ok, captured_exec):
    cmd_status()
    assert captured_exec[0][1][-1] == "ps"


def test_logs_execs_compose_logs(config_dir, _docker_ok, captured_exec):
    cmd_logs()
    assert captured_exec[0][1][-1] == "logs"


def test_status_requires_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "CONFIG_DIR", tmp_path)  # empty dir, no config
    with pytest.raises(SystemExit) as exc:
        cmd_status()
    assert exc.value.code == 1


def test_version_flag_prints_version(monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "_cli_version", lambda: "9.9.9")
    monkeypatch.setattr(cli_main.sys, "argv", ["trainable", "--version"])
    main()
    assert capsys.readouterr().out.strip() == "trainable 9.9.9"
