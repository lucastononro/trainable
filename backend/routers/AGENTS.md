# AGENTS.md — backend/routers

One FastAPI router module per resource. Routers are the HTTP edge of the backend.

## Layout

```
sessions.py       Session CRUD + message history
experiments.py    Experiment CRUD + lifecycle (created → prepping → ...)
projects.py       Project CRUD
models.py         Available model registry (proxies models.yml to the frontend)
registry.py       Registered model CRUD
snapshots.py      Run snapshots
compare.py        Multi-experiment compare
data_explorer.py  Dataset inspection + preview
lineage.py        Lineage graph endpoints (raw → processed → model)
notebook.py       Notebook read/write/run
files.py          Session/project file tree + download
s3_browser.py     S3 prefix listing
skills.py         Skill catalog (for the UI's "available tools" panel)
stream.py         SSE — the single subscription endpoint
usage.py          Token + cost rollups
```

## Core principles

1. **Thin routers.** Validate → call service → shape response. No business logic in route handlers.
2. **One resource per file.** If `sessions.py` is calling `experiments_service.do_thing`, you're crossing a boundary — that's fine, but the route belongs in whichever resource owns the URL.
3. **Async functions only.** Sync route handlers block the event loop and degrade SSE throughput.
4. **Pydantic schemas, not `dict[str, Any]`.** All request bodies and response models are declared in `backend/schemas.py`. Untyped routes have caused validation gaps that surfaced as runtime errors.
5. **Names, not IDs, in user-facing fields.** API responses can include both, but the UI displays `name`. If you only return `id`, you've forced the frontend to do an extra lookup.

## URL conventions

- **Plural collection routes**: `/api/experiments`, `/api/sessions`.
- **Singular item routes**: `/api/experiments/{id}`.
- **Nested resources only when ownership is mandatory**: `/api/sessions/{session_id}/messages`. Not `/api/messages?session_id=...`.
- **Actions as verbs at the end**: `/api/experiments/{id}/start`, `/api/sessions/{id}/abort`. Not `/api/experiments/start/{id}`.
- **SSE endpoint is `/api/stream/{session_id}`.** Single subscription per session — don't fragment streams.

## Error handling

- **Service-layer exceptions are the source of truth.** Routers don't compute error messages; they let `errors.py` map exceptions to HTTP status.
- **404 vs 403.** Resource doesn't exist → 404. Resource exists but caller can't see it → 403. Don't conflate; the difference matters for auth UX.
- **400 for validation, 422 for schema.** FastAPI defaults to 422 for Pydantic failures; we use 400 for our own validation (e.g., experiment state machine violations).
- **5xx is a bug.** Anything that returns 500 needs a ticket. Operational errors (S3 timeout, Modal unavailable) should be 503 with a retry hint.

## Streaming (`stream.py`)

- **One SSE endpoint per session.** The frontend subscribes once; the broadcaster fans events out.
- **Event types are well-defined**: `message_appended`, `lineage_updated`, `artifact_published`, `task_updated`, `metric_point`, `experiment_state_changed`. New event types go in `services/broadcaster.py:EventType`.
- **Don't push raw provider events to the frontend.** Translate to the frontend-friendly shapes first.
- **Heartbeats every 15s.** Browsers will close idle SSE connections; the heartbeat keeps them alive.

## Common pitfalls

- **Returning `dict` directly from a route.** Even if it works, the OpenAPI schema is wrong and the frontend can't generate types. Use a Pydantic response model.
- **Doing DB work in the route.** Move it to a service. Routes that grow past ~30 lines are a smell.
- **Spawning a background task with `asyncio.create_task` in the route.** The task gets garbage-collected when the response returns. Use `BackgroundTasks` from FastAPI or push the work to the broadcaster/runner.
- **Returning the full Message list for a long session.** Paginate. We've shipped routes that returned 50MB of message history.
- **Editing the same SSE endpoint to add a new event type instead of using the broadcaster.** The broadcaster is the integration point; the route just streams what the broadcaster publishes.
- **Authorization checks done client-side.** The frontend hides buttons based on state, but the backend re-verifies every action. Don't trust the request.

## Adding a new resource

1. **Add the SQLAlchemy model** in `backend/models.py`.
2. **Add Pydantic schemas** in `backend/schemas.py` (request + response).
3. **Add a service** under `backend/services/<resource>.py` for business logic.
4. **Add the router** under `backend/routers/<resource>.py`.
5. **Mount it** in `backend/main.py` under `routers`.
6. **Wire the frontend** — add types to `frontend/src/lib/types.ts` and API methods to `frontend/src/lib/api.ts`.
7. **Test the happy path + at least one error path.**

## Before you ship

- [ ] Async route handlers
- [ ] Pydantic request body + response model
- [ ] Business logic in a service, not the route
- [ ] Error path mapped via `errors.py`
- [ ] Response includes `name` where UI will display it
- [ ] Frontend types updated in `frontend/src/lib/types.ts`
- [ ] Mounted in `main.py`
