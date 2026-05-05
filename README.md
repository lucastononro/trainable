<p align="center">
  <img src="frontend/public/logo-with-text.png" alt="Trainable" height="60">
</p>

# Trainable

AI-powered ML experimentation platform. Upload a dataset, and AI agents autonomously perform EDA, data preparation, and model training — with real-time streaming visualization.

## Install

You need **Docker** and one of:
- [Anthropic API key](https://console.anthropic.com/) or Claude Pro/Max subscription
- [Modal](https://modal.com/) account (for sandboxed execution) — tokens at [modal.com/settings](https://modal.com/settings)

### One-liner (recommended)

```bash
# With uv (fast, isolated env per tool — preferred):
uv tool install trainable-ai

# Or with pip:
pip install trainable-ai

trainable init
```

The wizard walks you through everything: Docker check, API key setup, and launch.

### Docker Compose (manual)

```bash
docker pull ghcr.io/lucastononro/trainable-backend:latest
docker pull ghcr.io/lucastononro/trainable-frontend:latest
```

Then grab the compose file and configure:

```bash
curl -sLO https://raw.githubusercontent.com/lucastononro/trainable/main/docker-compose.prod.yml
curl -sLO https://raw.githubusercontent.com/lucastononro/trainable/main/.env.example
cp .env.example .env   # fill in ANTHROPIC_API_KEY, MODAL_TOKEN_ID, MODAL_TOKEN_SECRET
docker compose -f docker-compose.prod.yml up
```

Open [http://localhost:3000](http://localhost:3000).

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| MinIO Console | http://localhost:9001 (minioadmin/minioadmin) |

### Docker images

Multi-arch images (amd64 + arm64) are published to GitHub Container Registry:

```
ghcr.io/lucastononro/trainable-backend:latest
ghcr.io/lucastononro/trainable-frontend:latest
```

Pin a specific version with tags like `:v1.0.0` or a commit SHA.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes* | — | Claude API key (*or use subscription token below) |
| `CLAUDE_CODE_OAUTH_TOKEN` | Yes* | — | Claude Pro/Max token (*or use API key). Run `claude setup-token` to get it |
| `MODAL_TOKEN_ID` | Yes | — | Modal auth token ID |
| `MODAL_TOKEN_SECRET` | Yes | — | Modal auth token secret |
| `CLAUDE_MODEL` | No | `claude-sonnet-4-20250514` | Model for the AI agent |
| `DATABASE_URL` | No | SQLite | PostgreSQL connection string (set by docker-compose) |
| `S3_ENDPOINT` | No | localhost | S3-compatible endpoint (set by docker-compose) |

## How It Works

1. **Gallery** — Create experiments by uploading CSV/Parquet datasets
2. **Studio** — Split-pane workspace: chat with the AI agent (left) + canvas with reports, files, and live metrics (right)
3. **EDA** — Agent explores data quality, distributions, correlations, and generates a statistical report
4. **Prep** — Agent cleans, encodes, and splits data into train/val/test sets
5. **Train** — Agent trains and tunes models with live metrics streaming to a dashboard

## Tech Stack

- **Agent**: [Claude Agent SDK](https://docs.anthropic.com/en/docs/claude-agent-sdk) with custom MCP tools
- **Backend**: FastAPI + SQLAlchemy + SSE streaming
- **Frontend**: Next.js 14 + Tailwind + Recharts
- **Execution**: [Modal](https://modal.com/) sandboxes (isolated Python, optional GPU)
- **Storage**: S3/MinIO (artifacts) + Modal Volumes (workspace)
- **Database**: SQLite (dev) / PostgreSQL (prod)

## Development

<details>
<summary>For contributors who want to build from source</summary>

### Clone and configure

```bash
git clone https://github.com/lucastononro/trainable.git
cd trainable
cp .env.example .env
```

### Backend

```bash
cd backend
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Don't have uv? Install it once with `curl -LsSf https://astral.sh/uv/install.sh | sh`,
or fall back to `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Docker Compose (dev mode)

Runs PostgreSQL, MinIO, backend, and frontend with hot-reload and source mounts:

```bash
docker compose up
```

### Running Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/ -v
```

</details>

## Project Structure

```
cli/               CLI installer (uv tool install trainable-ai)
backend/           FastAPI application
  routers/         API endpoints (experiments, sessions, stream, files)
  services/        Agent orchestration, sandbox, broadcaster, validators
  tests/           pytest test suite
frontend/          Next.js application
  src/app/         Pages (gallery, studio)
  src/components/  React components (chat, canvas, metrics, modals)
  src/lib/         API client, SSE connector, types
docs/              Architecture and agent documentation
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed system design and [docs/agents.md](docs/agents.md) for agent documentation.

## CI/CD

- **CI** (`ci.yml`) — Ruff, pytest, Bandit, ESLint, TypeScript, Next.js build
- **Docker Images** (`publish-image.yml`) — Multi-arch images to GHCR on push to `main` and version tags
- **PyPI** (`publish.yml`) — CLI package to PyPI on version tags via trusted publisher
