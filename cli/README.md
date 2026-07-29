# trainable-ai

AI-powered ML experimentation platform — local installer.

## Install

```bash
pip install trainable-ai
```

## Usage

```bash
trainable init
```

Config lives at `~/.trainable/`, so `trainable up` / `trainable down` work
from any directory. Override the location with `TRAINABLE_HOME=...`.

The wizard will:
1. Check that Docker and the Compose plugin are installed
2. Write the production Docker Compose file to `~/.trainable/` (bundled inside
   the wheel, so installs work offline)
3. Prompt for your LLM provider keys — pick any of Claude, OpenAI, Gemini, or
   LiteLLM-routed backends (see Providers below)
4. Write a `.env` file
5. Start the full stack

## Providers

The backend treats all four LLM providers as equal peers. Collect **at least
one**; you can add more any time with `trainable reconfigure`.

| Provider | Env var(s) | How to get it |
|----------|------------|---------------|
| Claude (API key) | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| Claude (subscription) | `CLAUDE_CODE_OAUTH_TOKEN` | run `claude setup-token` |
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Gemini | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| LiteLLM | free-form, e.g. `GROQ_API_KEY`, `MISTRAL_API_KEY` | your backend's dashboard |

All setups also need Modal credentials for sandboxed code execution:
`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` from
[modal.com/settings](https://modal.com/settings).

## Commands

| Command | Description |
|---------|-------------|
| `trainable init` | Setup wizard — writes compose file, configures secrets, launches |
| `trainable reconfigure` | Add or replace LLM providers without losing existing keys |
| `trainable up` | Start all services (works from any directory) |
| `trainable down` | Stop all services |
| `trainable status` | Show running containers (`docker compose ps`) |
| `trainable logs` | Show service logs (`docker compose logs`) |
| `trainable --version` | Print the installed CLI version |

### Reconfiguring

Re-running `trainable init` on top of an existing config never clobbers your
other keys — it offers to add/replace a single provider or start over, and
preserves everything you don't touch. `trainable reconfigure` is a friendly
alias for the same flow.

## Version-pinned images

`trainable up` pins the backend and frontend images to the installed wheel's
version (e.g. `0.0.4`), so `pip install -U trainable-ai && trainable up` always
pulls the matching `ghcr.io/.../:<version>` rather than reusing a stale
`:latest` from your local Docker cache.

## Requirements

- Docker with the Compose plugin
- At least one LLM provider key (see Providers above)
- [Modal account](https://modal.com/) — tokens from
  [modal.com/settings](https://modal.com/settings)
