# AGENTS.md — cli

The `trainable-ai` PyPI package. Provides the `trainable` command: a wizard that checks Docker, sets up API keys, and launches the compose stack.

## Layout

```
pyproject.toml           Package metadata + entry point: `trainable = trainable_cli.main:cli`
trainable_cli/
  main.py                Click/Typer entry — dispatches to subcommands
  init.py                The `trainable init` wizard
  up.py / down.py        Compose lifecycle
  config.py              Reads / writes ~/.trainable/ config
  ...
README.md                User-facing install instructions
```

## Package name vs. command name

- **PyPI package**: `trainable-ai` (the dashed name).
- **Command**: `trainable` (no dash).

This split matters and has caused confusion. The pip install line is `pip install trainable-ai`; the wizard is `trainable init`. Don't conflate.

## Core principles

1. **The CLI never writes to the working directory.** All artifacts (compose files, env files, secrets, logs) go under `~/.trainable/`. The user can run `trainable up` from anywhere.
2. **The wizard is the source of truth for setup.** README documents what the wizard does, but the wizard is what actually configures things. If a step is in the README and not in the wizard, the wizard wins.
3. **Distribute multi-arch.** Docker images and the wheel both ship `amd64` + `arm64`. Apple Silicon users are first-class.
4. **One-liner install is the headline path.** `pip install trainable-ai && trainable init` should always work. Anything that breaks it is a P0 release blocker.
5. **Idempotent commands.** `trainable up` twice is fine. `trainable init` twice is fine — it detects existing config and offers to reconfigure.

## The wizard contract (`trainable init`)

The wizard, in order:

1. **Check prereqs.** Docker installed and running. Print actionable error if not.
2. **Pull images.** `ghcr.io/lucastononro/trainable-backend:latest` + `:frontend:latest`. Multi-arch manifest handles the platform.
3. **Collect credentials.** The backend treats Claude / OpenAI / Gemini / LiteLLM as equal peers (`backend/services/llm/factory.py`), so the wizard presents them as a flat multi-select and requires *at least one* on fresh install or full replace.
   - Claude: `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` (run `claude setup-token` for the latter).
   - OpenAI: `OPENAI_API_KEY`.
   - Gemini: `GEMINI_API_KEY` (or `GOOGLE_API_KEY`).
   - LiteLLM: free-form add-loop for backend-specific keys (`GROQ_API_KEY`, `MISTRAL_API_KEY`, etc.).
   - Required regardless: `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`.
4. **Write `~/.trainable/.env`** with permissions `0600`.
5. **Write `~/.trainable/docker-compose.yml`** (the production compose file).
6. **Launch.** `docker compose -f ~/.trainable/docker-compose.yml up -d`.
7. **Health check.** Poll `http://localhost:8000/health` until ready or timeout.
8. **Print URLs** and exit.

## Subcommands

- `trainable init` — the wizard above.
- `trainable up` — `docker compose up -d` against `~/.trainable/docker-compose.yml`.
- `trainable down` — stop and remove containers (data persists in volumes).
- `trainable logs [service]` — tail logs.
- `trainable doctor` — re-runs the prereq checks.
- `trainable uninstall` — removes containers, volumes, and `~/.trainable/`. Requires confirmation.

## Common pitfalls

- **Writing compose files into `cwd`.** They go under `~/.trainable/`. This is a release-blocker bug if it regresses.
- **Hardcoding the platform in image tags.** Use the multi-arch manifest, not `:latest-amd64`.
- **Forgetting to wire the entry point in `pyproject.toml`.** `pip install` succeeds but `trainable` returns "command not found." Always test `pip install -e . && trainable --help` in a clean venv before publishing.
- **Shipping wheels without the `[project.entry-points]` table populated.** Hatchling needs both `[project.scripts]` and a valid module path.
- **Asking for credentials we don't need.** If the user has `CLAUDE_CODE_OAUTH_TOKEN`, don't also nag for `ANTHROPIC_API_KEY`.
- **Logging secrets.** When writing the `.env`, never echo the value to stdout. Print `Saved <key>` (no value).
- **Updating `~/.trainable/.env` destructively without confirmation.** If `init` is re-run with config present, offer to keep / overwrite per key.

## Distribution

- **PyPI**: `hatch build && twine upload`.
- **Images**: `docker buildx build --platform linux/amd64,linux/arm64 ...`.
- **Both update on every release.** A release where the pip wheel pins old image tags is broken.
- **Pin major versions only.** `trainable-ai==0.x` pins to the major; image tags use semver.
- **Test install in a fresh venv** before tagging a release: `python -m venv /tmp/test && /tmp/test/bin/pip install trainable-ai && /tmp/test/bin/trainable --help`.

## Release checklist

- [ ] `pyproject.toml` version bumped
- [ ] Image tags published with new version + `:latest`
- [ ] Multi-arch manifest verified (`docker buildx imagetools inspect`)
- [ ] Wheel installs in a fresh venv
- [ ] `trainable --help` runs from the wheel
- [ ] `trainable init` runs end-to-end on a machine without prior config
- [ ] README and `RELEASE_NOTES.md` updated
