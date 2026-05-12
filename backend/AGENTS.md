# AGENTS.md — backend

FastAPI service. Owns: agent execution, skill dispatch, persistence, streaming, sandboxes.

## Layout

```
main.py            FastAPI app factory + router mount + startup hooks
config.py          Settings (env-driven, pydantic)
db.py              SQLAlchemy async engine + session factory
models.py          ORM models — Experiment, Session, Message, Artifact, ...
schemas.py         Pydantic request/response schemas
errors.py          Exception handlers
observability.py   OTel + structured logging context
routers/           One module per resource → see routers/AGENTS.md
services/          Business logic → see services/*/AGENTS.md
agents/            Agent YAML declarations → see agents/AGENTS.md
skills/            Skill implementations → see skills/AGENTS.md
scripts/           One-off operational scripts (migrations, backfills)
tests/             pytest — async via pytest-asyncio
```

## Core principles

1. **Async by default.** Every router function, every service that touches DB/S3/Modal is `async`. Sync helpers exist only for pure CPU work. If you're writing `def` instead of `async def` for an I/O function, you're probably wrong.
2. **Routers are thin.** A router function validates the request, calls a service, and shapes the response. Business logic lives in `services/`. If a router file is over 200 lines, the logic belongs in a service.
3. **Services own one concern.** `s3_client.py` doesn't talk to the DB. `experiments.py` doesn't talk to Modal directly. Cross-concern logic is composed in routers or in the agent runner.
4. **Persist truncated.** Tool results from `execute-code` can be megabytes. We cap each persisted thought block at 1500 chars (see `_THOUGHT_BLOCK_MAX_CHARS` in `runner.py`). Same rule applies anywhere you store agent output: store enough to debug, not the raw blob.
5. **Logs use `logger`, not `print`.** Every module starts with `logger = logging.getLogger(__name__)`. Use `bind_log_context` from `observability.py` for request/session-scoped context.
6. **Type-hint everything.** Function signatures, dataclass fields, dict shapes via TypedDict where applicable. `mypy` and `pyright` should both pass.

## Database

- **SQLAlchemy 2.x async syntax** — `select(Model).where(...)`, `await session.scalars(...)`. No legacy `Query` API.
- **Sessions come from `async_session()` in `db.py`** — use `async with async_session() as session:` and commit explicitly.
- **Migrations are not yet wired.** When you add a column, also add a startup migration in `db.py:init_db()` until we adopt Alembic. Don't skip this and ship — it'll break prod.
- **No raw SQL strings without a comment explaining why.** ORM first.

## Errors

- **Service-layer errors raise typed exceptions** (`ExperimentNotFound`, `PermissionDenied`, ...). Routers map them to HTTP status via `errors.py` handlers.
- **Never `except: pass`.** Either handle the exception and explain what to do, or let it propagate. Silent swallowing has bitten us multiple times (e.g., volume listings returning empty because `vol.reload()` was silently failing).
- **User-facing error messages don't leak IDs or stack traces.** The frontend gets a human-readable string. The trace lives in the log.

## Tests

- Tests live in `backend/tests/`. Run with `cd backend && pytest`.
- Use `pytest-asyncio` for async tests. Mark with `@pytest.mark.asyncio` only if `asyncio_mode = auto` is not set in `pyproject.toml`.
- **Test the failure paths.** If a function raises `PermissionDenied`, write a test that triggers it. The happy path is not enough.
- **Don't mock the database in tests that exercise schema.** Use SQLite or a real test DB. Mocked DB tests have shipped broken migrations before.

## Modal sandboxes

- All untrusted code (user data prep, training, EDA) runs in a Modal sandbox via `services/sandbox.py`.
- **The sandbox profile (CPU vs GPU, timeout, image) is configured in `services/sandbox.yml`**, not in code. Add new profiles there.
- **Sandboxes mount the `trainable` volume at `/data`.** Datasets go to `/data/datasets/{experiment_id}/`, session outputs to `/data/sessions/{session_id}/{stage}/`. Skills must respect this layout.
- **Modal SDK quirks**: not every `.aio()` helper exists in our SDK version. When in doubt, wrap sync Modal calls in `loop.run_in_executor`. See `services/sandbox.py` for the pattern.

## Observability

- **Every agent turn opens a span** via `agent_span()` in `observability.py`. Don't open spans inside services unless you're adding a new top-level capability.
- **Log context is bound per request** (`bind_log_context(session_id=..., experiment_id=...)`). All logs in that scope auto-include those fields. Clear it on exit.
- **Token + cost accounting flows through `services/usage.py`.** Don't write usage rows directly to the DB — call `record_llm_usage()` so cost computation stays consistent.

## Common pitfalls

- **Don't add a model row without filling required FK columns** (`project_id`, `session_id`, etc.). Missing FKs render as "orphan" nodes in the lineage canvas.
- **Don't call `vol.reload()` and assume it worked.** Use `reload_volume_async()` from `services/volume.py` — it handles the FastAPI-process edge case.
- **Don't bypass the broadcaster.** Frontend updates flow `service → broadcaster.publish → SSE → frontend`. Writing directly to the DB without publishing leaves the UI stale.
- **Don't accept `dict[str, Any]` as a route input.** Define a Pydantic schema in `schemas.py`. Untyped inputs have caused validation gaps that surfaced as runtime errors.
- **Don't write to the repo from a request handler.** Anything generated at runtime goes to the Modal volume or S3, never to a path inside the backend container's working dir.

## Before you ship

- [ ] `ruff check . && ruff format .` clean
- [ ] `pytest tests/ -v` passes
- [ ] New columns / tables migrated in `db.py:init_db()` (until Alembic lands)
- [ ] Logger used; no `print`
- [ ] Error path tested
- [ ] If you added a route, also added a Pydantic schema for body/response
