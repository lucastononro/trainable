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

No clone or build needed — just Docker.

```bash
# 1. Grab the compose file and env template
curl -sLO https://raw.githubusercontent.com/lucastononro/trainable/main/docker-compose.prod.yml
curl -sLO https://raw.githubusercontent.com/lucastononro/trainable/main/.env.example

# 2. Configure (set ANTHROPIC_API_KEY, MODAL_TOKEN_ID, MODAL_TOKEN_SECRET)
cp .env.example .env

# 3. Run
docker compose -f docker-compose.prod.yml up
```

Open [http://localhost:3000](http://localhost:3000).

You'll need:
- [Anthropic API key](https://console.anthropic.com/)
- [Modal](https://modal.com/) account → get tokens from [modal.com/settings](https://modal.com/settings)

## Development Setup

<details>
<summary>For contributors who want to build from source</summary>

### Prerequisites

- Python 3.11+
- Node.js 20+

### 1. Clone and configure

```bash
git clone https://github.com/lucastononro/trainable.git
cd trainable
cp .env.example .env
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

### Docker Compose (full stack, dev mode)

Runs PostgreSQL, MinIO, backend, and frontend with hot-reload:

```bash
docker compose up
```

</details>

This starts:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **MinIO Console**: http://localhost:9001 (minioadmin/minioadmin)

## Running Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/ -v
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key for the AI agent |
| `MODAL_TOKEN_ID` | Yes* | — | Modal auth (*or run `modal token set`) |
| `MODAL_TOKEN_SECRET` | Yes* | — | Modal auth |
| `DATABASE_URL` | No | SQLite (local file) | PostgreSQL connection string |
| `S3_ENDPOINT` | No | AWS S3 | S3-compatible endpoint (MinIO, etc.) |
| `AWS_ACCESS_KEY_ID` | No | — | S3 credentials |
| `AWS_SECRET_ACCESS_KEY` | No | — | S3 credentials |
| `CLAUDE_MODEL` | No | `claude-opus-4-6` | Model for the AI agent |

## Project Structure

```
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

## CI

GitHub Actions runs on every push to `main` and on pull requests:

- **Backend**: Ruff lint + format check, pytest, Bandit security scan
- **Frontend**: ESLint, TypeScript typecheck, Next.js build
