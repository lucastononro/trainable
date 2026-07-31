"""Trainable CLI — setup wizard and launcher."""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.request
import webbrowser
from importlib import metadata, resources
from pathlib import Path

COMPOSE_FILE = "docker-compose.prod.yml"
ENV_FILE = ".env"
PROJECT_NAME = "trainable"
PACKAGE_NAME = "trainable-ai"

# Config lives in ~/.trainable so `trainable up` works from any directory.
# Override via TRAINABLE_HOME env var for advanced users.
CONFIG_DIR = Path(os.environ.get("TRAINABLE_HOME", Path.home() / ".trainable"))

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"


def banner():
    print(
        textwrap.dedent(f"""\

        {BOLD}╔══════════════════════════════════════╗
        ║          trainable                   ║
        ║   AI-powered ML experimentation      ║
        ╚══════════════════════════════════════╝{RESET}
    """)
    )


def step(n: int, total: int, msg: str):
    print(f"\n{CYAN}{BOLD}[{n}/{total}]{RESET} {BOLD}{msg}{RESET}")


def success(msg: str):
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg: str):
    print(f"  {YELLOW}!{RESET} {msg}")


def fail(msg: str):
    print(f"  {RED}✗{RESET} {msg}")


def prompt_secret(label: str, help_url: str | None = None) -> str:
    hint = f" {DIM}({help_url}){RESET}" if help_url else ""
    while True:
        value = getpass.getpass(f"  {label}{hint}: ").strip()
        if value:
            return value
        warn("Cannot be empty, try again.")


def prompt_choice(label: str, options: list[str]) -> int:
    print(f"  {label}")
    for i, opt in enumerate(options, 1):
        print(f"    {BOLD}{i}{RESET}) {opt}")
    while True:
        raw = input(f"  {DIM}Enter choice [1-{len(options)}]{RESET}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        warn(f"Please enter a number between 1 and {len(options)}")


def _cli_version() -> str:
    """Return the installed wheel's version (matches the published image tag).

    Falls back to "latest" if the package can't be introspected — happens
    when running from a source checkout that hasn't been installed.
    """
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return "latest"


def _write_compose_template(dest: Path) -> None:
    """Write `docker-compose.prod.yml` from the template bundled in the wheel.

    Called on every `trainable init` *and* every `trainable up` so existing
    installs migrate to the env-var-tag-aware compose layout automatically.
    """
    template = resources.files("trainable_cli").joinpath("_templates", COMPOSE_FILE)
    (dest / COMPOSE_FILE).write_text(template.read_text(encoding="utf-8"))


def write_compose_from_template(dest: Path) -> None:
    _write_compose_template(dest)
    success(f"Wrote {COMPOSE_FILE}")


def check_docker():
    if not shutil.which("docker"):
        fail("Docker not found. Install it from https://docs.docker.com/get-docker/")
        sys.exit(1)
    result = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail("Docker Compose not found. Install Docker Desktop or the compose plugin.")
        sys.exit(1)
    success("Docker and Docker Compose found")


def check_docker_daemon():
    """Preflight: fail fast with friendly guidance when the Docker daemon
    is installed but not running — otherwise `docker compose` surfaces a
    raw connection error (#126)."""
    if not shutil.which("docker"):
        fail("Docker not found. Install it from https://docs.docker.com/get-docker/")
        sys.exit(1)
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(
            "Docker is installed but the daemon is not running. "
            "Start Docker Desktop (or the docker service) and try again."
        )
        sys.exit(1)


# Names the wizard knows about explicitly; everything else is treated as a
# LiteLLM backend key in the free-form section.
_KNOWN_PROVIDER_KEYS = {
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "COMPUTE_PROVIDER",
    "RUNPOD_API_KEY",
    "RUNPOD_S3_ACCESS_KEY_ID",
    "RUNPOD_S3_SECRET_ACCESS_KEY",
    "RUNPOD_DATACENTER_ID",
    "RUNPOD_NETWORK_VOLUME_ID",
    "RUNPOD_WORKER_IMAGE",
}


def read_env(dest: Path) -> dict[str, str]:
    """Parse an existing .env into a dict. Best-effort, comment-aware.

    Used so `trainable init` on top of an existing config can preserve the
    user's existing keys instead of forcing them to re-enter everything.
    """
    env_path = dest / ENV_FILE
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def write_env(dest: Path, config: dict[str, str]):
    lines = ["# Generated by: trainable init", ""]

    if config.get("ANTHROPIC_API_KEY"):
        lines += [
            "# Claude authentication (API key)",
            f"ANTHROPIC_API_KEY={config['ANTHROPIC_API_KEY']}",
        ]
    elif config.get("CLAUDE_CODE_OAUTH_TOKEN"):
        lines += [
            "# Claude authentication (subscription token — uses your Pro/Max quota)",
            f"CLAUDE_CODE_OAUTH_TOKEN={config['CLAUDE_CODE_OAUTH_TOKEN']}",
        ]

    if config.get("OPENAI_API_KEY"):
        lines += ["", "# OpenAI", f"OPENAI_API_KEY={config['OPENAI_API_KEY']}"]
    if config.get("GEMINI_API_KEY"):
        lines += ["", "# Gemini", f"GEMINI_API_KEY={config['GEMINI_API_KEY']}"]
    if config.get("GOOGLE_API_KEY") and not config.get("GEMINI_API_KEY"):
        # Either name works; only emit GOOGLE_API_KEY when GEMINI_API_KEY is unset.
        lines += [
            "",
            "# Gemini (alt env var)",
            f"GOOGLE_API_KEY={config['GOOGLE_API_KEY']}",
        ]

    # Generic LiteLLM-routed backend keys (free-form add-loop in the wizard).
    extra_keys = sorted(k for k in config.keys() if k not in _KNOWN_PROVIDER_KEYS)
    if extra_keys:
        lines += ["", "# LiteLLM backends"]
        for k in extra_keys:
            lines.append(f"{k}={config[k]}")

    lines += [
        "",
        "# Compute provider (sandboxes, notebook kernels, deployments)",
        f"COMPUTE_PROVIDER={config.get('COMPUTE_PROVIDER', 'modal')}",
        "",
        "# Modal (used when COMPUTE_PROVIDER=modal)",
        f"MODAL_TOKEN_ID={config.get('MODAL_TOKEN_ID', '')}",
        f"MODAL_TOKEN_SECRET={config.get('MODAL_TOKEN_SECRET', '')}",
    ]

    if config.get("RUNPOD_API_KEY") or config.get("COMPUTE_PROVIDER") == "runpod":
        lines += [
            "",
            "# RunPod (used when COMPUTE_PROVIDER=runpod)",
            f"RUNPOD_API_KEY={config.get('RUNPOD_API_KEY', '')}",
            f"RUNPOD_S3_ACCESS_KEY_ID={config.get('RUNPOD_S3_ACCESS_KEY_ID', '')}",
            f"RUNPOD_S3_SECRET_ACCESS_KEY={config.get('RUNPOD_S3_SECRET_ACCESS_KEY', '')}",
            f"RUNPOD_DATACENTER_ID={config.get('RUNPOD_DATACENTER_ID', 'US-KS-2')}",
        ]
        if config.get("RUNPOD_NETWORK_VOLUME_ID"):
            lines.append(
                f"RUNPOD_NETWORK_VOLUME_ID={config['RUNPOD_NETWORK_VOLUME_ID']}"
            )
        if config.get("RUNPOD_WORKER_IMAGE"):
            lines.append(f"RUNPOD_WORKER_IMAGE={config['RUNPOD_WORKER_IMAGE']}")

    env_path = dest / ENV_FILE
    # Secrets file: restrict to owner-only so other users on a shared machine
    # can't read the plaintext API keys/tokens. Create with O_CREAT mode 0o600
    # so the file is never visible at a looser permission even for an instant
    # (avoids a TOCTOU window vs. write-then-chmod). The chmod afterwards
    # tightens pre-existing files, where the O_CREAT mode doesn't apply.
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(env_path, 0o600)
    success(f"Wrote {ENV_FILE}")


def configured_providers(config: dict[str, str]) -> list[str]:
    """Return human-readable list of providers that look configured."""
    providers: list[str] = []
    if config.get("CLAUDE_CODE_OAUTH_TOKEN"):
        providers.append("Claude (subscription OAuth)")
    elif config.get("ANTHROPIC_API_KEY"):
        providers.append("Claude (API key)")
    if config.get("OPENAI_API_KEY"):
        providers.append("OpenAI")
    if config.get("GEMINI_API_KEY") or config.get("GOOGLE_API_KEY"):
        providers.append("Gemini")
    litellm_keys = sorted(
        k
        for k in config.keys()
        if k not in _KNOWN_PROVIDER_KEYS and k.endswith("_API_KEY")
    )
    if litellm_keys:
        backends = [k.removesuffix("_API_KEY").lower() for k in litellm_keys]
        providers.append(f"LiteLLM ({', '.join(backends)})")
    return providers


def _has_compute_creds(config: dict[str, str]) -> bool:
    """True when the configured compute provider has its credentials."""
    provider = (config.get("COMPUTE_PROVIDER") or "modal").lower()
    if provider == "runpod":
        return bool(
            config.get("RUNPOD_API_KEY")
            and config.get("RUNPOD_S3_ACCESS_KEY_ID")
            and config.get("RUNPOD_S3_SECRET_ACCESS_KEY")
        )
    return bool(config.get("MODAL_TOKEN_ID") and config.get("MODAL_TOKEN_SECRET"))


def prompt_compute_provider(existing: dict[str, str]) -> dict[str, str]:
    """Pick the GPU cloud that runs sandboxes/kernels/deployments and
    collect its credentials. Existing keys are kept and not re-prompted."""
    print()
    choice = prompt_choice(
        "Which compute provider should run sandboxes and deployments?",
        ["Modal (default)", "RunPod"],
    )
    out: dict[str, str] = {}
    if choice == 1:
        out["COMPUTE_PROVIDER"] = "modal"
        if existing.get("MODAL_TOKEN_ID") and existing.get("MODAL_TOKEN_SECRET"):
            return out
        print()
        print(f"  {DIM}Get your Modal tokens from https://modal.com/settings{RESET}\n")
        out["MODAL_TOKEN_ID"] = prompt_secret("Modal Token ID")
        out["MODAL_TOKEN_SECRET"] = prompt_secret("Modal Token Secret")
        return out

    out["COMPUTE_PROVIDER"] = "runpod"
    print()
    print(
        f"  {DIM}RunPod needs two key pairs from https://console.runpod.io:{RESET}\n"
        f"  {DIM}  1. an API key      (Settings → API Keys){RESET}\n"
        f"  {DIM}  2. an S3 API key   (Settings → S3 API Keys — for the network volume){RESET}\n"
    )
    out["RUNPOD_API_KEY"] = existing.get("RUNPOD_API_KEY") or prompt_secret(
        "RunPod API Key"
    )
    out["RUNPOD_S3_ACCESS_KEY_ID"] = existing.get(
        "RUNPOD_S3_ACCESS_KEY_ID"
    ) or prompt_secret("RunPod S3 Access Key ID")
    out["RUNPOD_S3_SECRET_ACCESS_KEY"] = existing.get(
        "RUNPOD_S3_SECRET_ACCESS_KEY"
    ) or prompt_secret("RunPod S3 Secret Access Key")
    default_dc = existing.get("RUNPOD_DATACENTER_ID") or "US-KS-2"
    print(
        f"  {DIM}Datacenter must support the S3 API (e.g. US-KS-2, EU-RO-1, "
        f"EU-CZ-1, EUR-IS-1).{RESET}"
    )
    dc = input(f"  RunPod datacenter {DIM}[{default_dc}]{RESET}: ").strip()
    out["RUNPOD_DATACENTER_ID"] = dc or default_dc
    return out


def prompt_claude_auth() -> dict[str, str]:
    print()
    choice = prompt_choice(
        "How would you like to authenticate with Claude?",
        [
            "Anthropic API key",
            "Claude subscription (Pro/Max) token",
        ],
    )
    print()

    if choice == 1:
        key = prompt_secret(
            "Anthropic API key",
            "https://console.anthropic.com",
        )
        return {"ANTHROPIC_API_KEY": key}

    # Subscription token flow
    print("  To get your subscription token, run this in another terminal:\n")
    print(f"    {BOLD}claude setup-token{RESET}\n")
    print("  Then paste the token below.\n")
    token = prompt_secret("Claude subscription token")
    return {"CLAUDE_CODE_OAUTH_TOKEN": token}


def prompt_openai_auth() -> dict[str, str]:
    print()
    key = prompt_secret("OpenAI API key", "https://platform.openai.com/api-keys")
    return {"OPENAI_API_KEY": key}


def prompt_gemini_auth() -> dict[str, str]:
    print()
    key = prompt_secret("Gemini API key", "https://aistudio.google.com/apikey")
    return {"GEMINI_API_KEY": key}


def prompt_litellm_keys() -> dict[str, str]:
    """Free-form add-loop for LiteLLM backend API keys (Groq, Mistral, etc.)."""
    print()
    print("  LiteLLM routes calls to many backends — each needs its own key env var.")
    print(
        f"  {DIM}Examples: GROQ_API_KEY, MISTRAL_API_KEY, TOGETHER_API_KEY, DEEPSEEK_API_KEY{RESET}\n"
    )
    out: dict[str, str] = {}
    while True:
        name = input(f"  Backend env var name {DIM}(blank to finish){RESET}: ").strip()
        if not name:
            break
        if not name.replace("_", "").isalnum():
            warn("Invalid env var name — letters / digits / underscores only.")
            continue
        out[name.upper()] = prompt_secret(f"{name.upper()} value")
    return out


def prompt_providers(*, required: bool) -> dict[str, str]:
    """Multi-select LLM provider picker.

    The backend treats Claude / OpenAI / Gemini / LiteLLM as equal peers, so
    the wizard offers them as one flat list. When `required` is True (fresh
    install or full-replace flow) the prompt loops until the user picks at
    least one; otherwise (the "add to existing config" flow) zero picks is
    fine — the existing keys stay.
    """
    options = [
        ("claude", "Claude (Anthropic API key or subscription OAuth)"),
        ("openai", "OpenAI (API key)"),
        ("gemini", "Gemini (API key)"),
        ("litellm", "LiteLLM (catch-all: Groq, Mistral, DeepSeek, etc.)"),
    ]
    handlers = {
        "claude": prompt_claude_auth,
        "openai": prompt_openai_auth,
        "gemini": prompt_gemini_auth,
        "litellm": prompt_litellm_keys,
    }
    hint = "at least one required" if required else "blank to skip"

    while True:
        print("\n  Select providers to configure (space-separated numbers):")
        for i, (_, label) in enumerate(options, 1):
            print(f"    {BOLD}{i}{RESET}) {label}")
        raw = input(f"  {DIM}Choices [1-{len(options)}] ({hint}){RESET}: ").strip()
        selected: list[str] = []
        for tok in raw.replace(",", " ").split():
            if tok.isdigit() and 1 <= int(tok) <= len(options):
                selected.append(options[int(tok) - 1][0])

        if not selected:
            if required:
                warn("Need at least one LLM provider — pick one or more above.")
                continue
            return {}

        out: dict[str, str] = {}
        for key in selected:
            out.update(handlers[key]())
        return out


def _existing_config_choice(existing: dict[str, str]) -> str:
    """Ask what to do when ~/.trainable/.env already has values.

    Returns one of: "add" | "replace" | "cancel".
    """
    print()
    providers = configured_providers(existing)
    if providers:
        print(f"  {BOLD}Current config has:{RESET}")
        for p in providers:
            print(f"    {GREEN}✓{RESET} {p}")
        if existing.get("MODAL_TOKEN_ID"):
            print(f"    {GREEN}✓{RESET} Modal credentials")
        if existing.get("RUNPOD_API_KEY"):
            print(f"    {GREEN}✓{RESET} RunPod credentials")
        provider = (existing.get("COMPUTE_PROVIDER") or "modal").lower()
        print(f"    {GREEN}✓{RESET} Compute provider: {provider}")
    else:
        print(f"  {DIM}Existing .env appears empty.{RESET}")
    print()
    choice = prompt_choice(
        "What would you like to do?",
        [
            "Add or replace a provider (keeps everything else)",
            "Replace the entire config (start over)",
            "Cancel",
        ],
    )
    return {1: "add", 2: "replace", 3: "cancel"}[choice]


def cmd_init():
    banner()

    dest = CONFIG_DIR
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    # `mode` is ignored when the directory already exists, so chmod explicitly
    # to tighten a pre-existing loose (e.g. 0755) config dir holding secrets.
    os.chmod(dest, 0o700)
    print(f"  {DIM}Config directory: {dest}{RESET}")

    # Step 1 — check Docker
    step(1, 4, "Checking prerequisites")
    check_docker()

    # Step 2 — write compose file from the wheel's bundled template
    step(2, 4, "Writing docker-compose.prod.yml")
    if (dest / COMPOSE_FILE).exists():
        warn(f"{COMPOSE_FILE} already exists, overwriting")
    write_compose_from_template(dest)

    # Step 3 — wizard. If config exists, give the user three paths so they
    # don't lose previously-entered keys when adding a new provider.
    step(3, 4, "Configuring secrets")

    existing = read_env(dest)
    config: dict[str, str]
    mode: str

    if existing:
        mode = _existing_config_choice(existing)
        if mode == "cancel":
            print(f"\n  Run {BOLD}trainable up{RESET} when you're ready.\n")
            return
        if mode == "add":
            # Carry forward everything; only re-prompt for what the user picks.
            config = dict(existing)
            print()
            print("  Pick what to add or replace; existing keys are preserved.")
            config.update(prompt_providers(required=False))
            # Compute — only re-prompt when the configured provider is
            # missing its credentials.
            if not _has_compute_creds(config):
                print()
                print(
                    f"  {DIM}Compute credentials missing — needed for sandbox execution.{RESET}"
                )
                config.update(prompt_compute_provider(config))
        else:  # replace
            config = {}
            config.update(prompt_providers(required=True))
            config.update(prompt_compute_provider(config))
    else:
        # Fresh install path
        config = {}
        config.update(prompt_providers(required=True))
        config.update(prompt_compute_provider(config))

    print()
    write_env(dest, config)

    # Summary so the user can see what they ended up with.
    providers = configured_providers(config)
    if providers:
        print()
        print(f"  {BOLD}Configured providers:{RESET}")
        for p in providers:
            print(f"    {GREEN}✓{RESET} {p}")
    print(
        f"    {GREEN}✓{RESET} Compute: "
        f"{(config.get('COMPUTE_PROVIDER') or 'modal').lower()}"
    )
    print(
        f"\n  {DIM}Tip: re-run {BOLD}trainable init{RESET}{DIM} (or "
        f"{BOLD}trainable reconfigure{RESET}{DIM}) anytime to add more keys.{RESET}"
    )

    # Step 4 — launch
    step(4, 4, "Ready to launch")
    print()
    answer = input(f"  Start trainable now? {DIM}[Y/n]{RESET}: ").strip().lower()
    if answer in ("", "y", "yes"):
        cmd_up()
    else:
        print(f"\n  Run {BOLD}trainable up{RESET} from anywhere when you're ready.\n")


def cmd_reconfigure():
    """Friendly alias for `trainable init` when config already exists."""
    if not (CONFIG_DIR / ENV_FILE).exists():
        warn(f"No existing config at {CONFIG_DIR}. Running fresh init.")
    cmd_init()


def _compose_args(subcommand: list[str]) -> list[str]:
    """Build `docker compose` args pinned to the global config directory."""
    compose = CONFIG_DIR / COMPOSE_FILE
    return [
        "docker",
        "compose",
        "--project-name",
        PROJECT_NAME,
        "--project-directory",
        str(CONFIG_DIR),
        "-f",
        str(compose),
        *subcommand,
    ]


def _require_config():
    compose = CONFIG_DIR / COMPOSE_FILE
    env = CONFIG_DIR / ENV_FILE
    if not compose.exists() or not env.exists():
        fail(
            f"Config not found at {CONFIG_DIR}. Run {BOLD}trainable init{RESET} first."
        )
        sys.exit(1)
    # Daemon-liveness preflight so a stopped Docker produces actionable
    # guidance instead of a raw `docker compose` connection error (#126).
    check_docker_daemon()


FRONTEND_URL = "http://localhost:3000"


def _wait_and_open_browser(url: str, timeout: float = 60.0) -> None:
    """Poll `url` until it responds, then open it. Silent on failure — the
    user has the URL printed in the parent and can open it manually."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.5)


def _maybe_spawn_browser_opener(url: str) -> None:
    """Fork a child that opens `url` once it's reachable. The parent then
    execs into `docker compose up` so log streaming and Ctrl+C behavior are
    unchanged. No-op on platforms without fork (Windows) or when the user
    opts out."""
    if "--no-browser" in sys.argv or os.environ.get("TRAINABLE_NO_BROWSER"):
        return
    if not hasattr(os, "fork"):
        return
    try:
        pid = os.fork()
    except OSError:
        return
    if pid == 0:
        try:
            _wait_and_open_browser(url)
        finally:
            os._exit(0)


def cmd_up():
    _require_config()

    # Pin the docker images to this CLI's version: the published image tag
    # `:0.0.4` ships from the same release that produced this wheel. Without
    # this, the compose file's `:${TRAINABLE_*_TAG:-latest}` defaults would
    # pick up whichever stale `:latest` is in the user's docker cache.
    version = _cli_version()
    os.environ.setdefault("TRAINABLE_BACKEND_TAG", version)
    os.environ.setdefault("TRAINABLE_FRONTEND_TAG", version)

    # Refresh the compose file from the wheel's bundled template every time —
    # users who upgraded the wheel but never re-ran `init` would otherwise
    # keep an old compose layout (no env-var-tag indirection) and miss the
    # version pinning entirely. Quiet by default; init handles the noisy path.
    _write_compose_template(CONFIG_DIR)

    print(f"\n{GREEN}{BOLD}Starting trainable {version}...{RESET}")
    print(f"{DIM}  Frontend:      {FRONTEND_URL}")
    print("  Backend API:   http://localhost:8000")
    print(f"  MinIO Console: http://localhost:9001{RESET}\n")

    _maybe_spawn_browser_opener(FRONTEND_URL)
    os.execvp("docker", _compose_args(["up"]))


def cmd_down():
    _require_config()
    os.execvp("docker", _compose_args(["down"]))


def cmd_status():
    """Show the stack's containers (`docker compose ps`)."""
    _require_config()
    os.execvp("docker", _compose_args(["ps"]))


def cmd_logs():
    """Dump the stack's logs (`docker compose logs`)."""
    _require_config()
    os.execvp("docker", _compose_args(["logs"]))


USAGE = f"""\
{BOLD}trainable{RESET} — AI-powered ML experimentation platform

{BOLD}Usage:{RESET}
  trainable init           First-time setup wizard (writes config to ~/.trainable)
  trainable reconfigure    Add or replace LLM providers without losing existing keys
  trainable up             Start all services (works from any directory)
                           Opens {FRONTEND_URL} in your browser once ready.
                           Pass --no-browser (or set TRAINABLE_NO_BROWSER=1) to skip.
  trainable down           Stop all services
  trainable status         Show running containers (docker compose ps)
  trainable logs           Show service logs (docker compose logs)
  trainable --version      Print the installed CLI version

{BOLD}Quick start:{RESET}
  pip install trainable-ai
  trainable init
"""


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else None

    if cmd == "init":
        cmd_init()
    elif cmd in ("reconfigure", "config"):
        cmd_reconfigure()
    elif cmd == "up":
        cmd_up()
    elif cmd == "down":
        cmd_down()
    elif cmd == "status":
        cmd_status()
    elif cmd == "logs":
        cmd_logs()
    elif cmd in ("--version", "version"):
        print(f"trainable {_cli_version()}")
    else:
        print(USAGE)
        sys.exit(0 if cmd in ("-h", "--help") else 1)


if __name__ == "__main__":
    main()
