<p align="center">
  <img src="frontend/public/logo-with-text.png" alt="Trainable" height="60">
</p>

# Trainable

AI-powered ML experimentation platform. Upload a dataset, and AI agents autonomously perform EDA, data preparation, and model training — with real-time streaming visualization.

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

## Quick Start

### Option A: CLI wizard (recommended)

```bash
pip install trainable
mkdir trainable && cd trainable
trainable init
```

The wizard checks Docker, downloads the compose file, prompts for your API keys, and starts everything.

### Option B: Manual setup

```bash
curl -sLO https://raw.githubusercontent.com/lucastononro/trainable/main/docker-compose.prod.yml
curl -sLO https://raw.githubusercontent.com/lucastononro/trainable/main/.env.example
cp .env.example .env   # set ANTHROPIC_API_KEY, MODAL_TOKEN_ID, MODAL_TOKEN_SECRET
docker compose -f docker-compose.prod.yml up
```

Once running, open [http://localhost:3000](http://localhost:3000).

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| MinIO Console | http://localhost:9001 (minioadmin/minioadmin) |

### Prerequisites

- Docker with Compose plugin
- [Anthropic API key](https://console.anthropic.com/) or a Claude Pro/Max subscription
- [Modal](https://modal.com/) account — get tokens from [modal.com/settings](https://modal.com/settings)

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes* | — | Claude API key (*or use `CLAUDE_CODE_OAUTH_TOKEN`) |
| `CLAUDE_CODE_OAUTH_TOKEN` | Yes* | — | Claude subscription token (*or use API key). Run `claude setup-token` to get it |
| `MODAL_TOKEN_ID` | Yes | — | Modal auth |
| `MODAL_TOKEN_SECRET` | Yes | — | Modal auth |
| `CLAUDE_MODEL` | No | `claude-sonnet-4-20250514` | Model for the AI agent |
| `DATABASE_URL` | No | SQLite | PostgreSQL connection string (set by docker-compose) |
| `S3_ENDPOINT` | No | localhost | S3-compatible endpoint (set by docker-compose) |

## Development Setup

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
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

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
cli/               CLI installer (pip install trainable)
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

GitHub Actions workflows:

- **CI** (`ci.yml`) — Runs on every push to `main` and PRs: Ruff lint + format, pytest, Bandit security scan, ESLint, TypeScript typecheck, Next.js build
- **Docker Images** (`publish-image.yml`) — Builds and pushes multi-arch (amd64 + arm64) images to GHCR on push to `main` and version tags
- **PyPI** (`publish.yml`) — Publishes the CLI package to PyPI on version tags via trusted publisher
