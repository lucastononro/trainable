# Contributing to Trainable

Thanks for your interest in contributing! This guide will help you get started.

## Getting Started

See the [README](README.md) for prerequisites and quick start instructions.

## Development Setup

### Backend

```bash
cd backend
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
```

(`pip install -r requirements.txt` still works if you don't have uv —
install uv once with `curl -LsSf https://astral.sh/uv/install.sh | sh`.)

### Frontend

```bash
cd frontend
npm ci
```

### Pre-commit Hooks

We use [pre-commit](https://pre-commit.com/) to run checks before each commit:

```bash
uv tool install pre-commit  # or: pip install pre-commit
pre-commit install
```

This automatically runs ruff (lint + format), trailing whitespace fixes, and private key detection on every commit.

## Code Style

### Python (backend)

- Formatter/linter: [Ruff](https://docs.astral.sh/ruff/) (configured in `pyproject.toml`)
- Run manually: `cd backend && ruff check . && ruff format .`
- Type hints are expected on all function signatures
- Use `logger` (not `print`) for all logging

### TypeScript (frontend)

- Linter: ESLint via `next lint`
- Formatter: Prettier (configured in `frontend/.prettierrc`)
- Run manually: `cd frontend && npm run lint && npm run format:check`
- Use proper TypeScript types (avoid `any`)

## Running Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/ -v
```

All tests must pass before submitting a PR.

## Pull Request Process

1. **Branch**: Create a branch from `main` with a descriptive name (e.g., `feat/add-gpu-selector`, `fix/sse-reconnect`)
2. **Commits**: Use [conventional commits](https://www.conventionalcommits.org/):
   - `feat:` new feature
   - `fix:` bug fix
   - `docs:` documentation
   - `refactor:` code restructuring
   - `test:` adding/updating tests
   - `style:` formatting (no logic change)
   - `ci:` CI/CD changes
3. **CI**: All checks must pass (lint, tests, typecheck, build)
4. **Review**: Request a review from a maintainer
5. **Merge**: Squash and merge once approved

## Reporting Issues

- Use [GitHub Issues](https://github.com/lucastononro/trainable-monorepo/issues)
- Include steps to reproduce, expected vs actual behavior, and environment details
- For security vulnerabilities, please email the maintainer directly instead of opening a public issue
