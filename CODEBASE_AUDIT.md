# Trainable — Full Codebase Audit

**Scope:** entire repository (backend + frontend + infra), not just the open PR.

**Method:** three parallel code-review agents scanned the codebase in depth, then every high-priority finding was verified against the actual code before landing in this file. Unverified or overstated claims are called out in a dedicated section so reviewers know why they didn't make the cut.

---

## 🔴 High-priority — fix before shipping

### B-1. `_running_tasks` dict is unprotected shared state
**File:** `backend/services/agent/tasks.py:8-9` and `backend/routers/sessions.py:146, 284`

Two concurrent requests for the same `session_id` can both pass the `existing and not existing.done()` guard and both call `asyncio.create_task(...)`. The second write to `_running_tasks[session_id]` silently overwrites the first task reference — the first `asyncio.Task` becomes an orphan that the session's `cancel()` call cannot reach. It keeps running, holds its Modal sandbox slot, and `cleanup_session` never touches it.

**Fix:** serialize task creation with an `asyncio.Lock` per session, or use an atomic "test-and-set" on `_running_tasks` inside a single event-loop tick. The `_session_semaphores` dict in `clarifications.py` has the same concurrent-creation pattern.

### B-2. Frontend ref collisions — same ref attached to two live nodes
**File:** `frontend/src/app/page.tsx:191, 1286, 1448` (attachMenuRef); `:1299, 1462` (fileInputRef2)

`attachMenuRef` is attached to a `<div>` in the welcome-screen input bar AND a `<div>` in the studio input bar. A React `ref` object can only hold one node; whichever renders last wins. The outside-click handler that reads `attachMenuRef.current.contains(...)` works against only one of the two panels at any time.

**Note:** on inspection, the two panels live in a ternary (`!hasActiveSession ? welcome : studio`) — so they are **not both mounted at the same time**. In the current layout only one node holds the ref at any given moment, which is why the bug hasn't surfaced. This is still fragile: if anyone flattens the ternary later, the bug instantly re-appears.

**Fix:** split into `welcomeAttachMenuRef` and `chatAttachMenuRef`. Same for `fileInputRef2`. 5-minute change that prevents the footgun.

### B-3. `code_output` SSE handler calls `addItem` inside a `setChatItems` updater
**File:** `frontend/src/app/page.tsx:344`

```ts
setChatItems((prev) => {
  const idx = prev.findLastIndex((i) => i.type === 'tool_start');
  if (idx >= 0) { ... return updated; }
  addItem({ type: 'code_output', ... });  // <-- setter called inside another setter's updater
  return prev;
});
```

Calling a state setter inside another setter's updater is an explicit React anti-pattern. Under Strict Mode the updater function runs **twice** to detect impure updaters — meaning `addItem` fires twice. In production, even single-invocation ordering is unreliable.

**Fix:** hoist the fallback to an outer `if` after reading `chatItemsRef`-style, or restructure to always return a new array in one place.

### B-4. S3 `list_objects_v2` never paginates — silently truncates at 1000 objects
**File:** `backend/routers/experiments.py:183-215, 322-358`

`create_experiment_from_s3` and the S3 attach path both call `s3.list_objects_v2(Bucket=..., Prefix=...)` and iterate `response.get("Contents", [])`. S3 returns at most 1000 keys per call and signals more via `NextContinuationToken`, which the code ignores. For a prefix with more than 1000 files, only the first page is synced to the Modal Volume — with no warning.

**Fix:** use `s3.get_paginator("list_objects_v2")` and flatten all pages.

### B-5. Live-secret scare — was it actually committed?
**File:** `.env:2`

The backend-data agent flagged the OAuth token in `.env` as "committed to version control and actively compromised". **Verified: `.env` is NOT tracked by git** (`git ls-files .env` returns nothing; only the working tree contains it). The token is not in the repository history.

**However**: the token IS present on disk and would be read from `.env` by `pydantic_settings`. Confirm `.env` is listed in `.gitignore` and that no branch ever accidentally committed it. This is worth double-checking because the reviewer's instinct was right about the risk class even if the specific claim was wrong.

**Action:** verify `.gitignore` excludes `.env`, rotate the token on principle if this repo is or will be public.

### B-6. Start-stage guard → task-register has a TOCTOU window
**File:** `backend/routers/sessions.py:184-290`

The flow is: check `_running_tasks[session_id]` → `await db.commit()` → later `_running_tasks[session_id] = asyncio.create_task(_run_agent())`. Two requests arriving during the `await db.commit()` yield point both pass the guard, both commit `state = {stage}_running`, and both create tasks. The last task write to `_running_tasks` wins; the first is orphaned (same pathology as B-1).

**Fix:** register the task before the first `await`, or share an `asyncio.Lock` between the guard check and the register.

### B-7. Orphaned frontend components inflate the bundle
**Files:** `frontend/src/components/Studio.tsx`, `ChatPanel.tsx`, `CanvasPanel.tsx`, `MessageBubble.tsx`, `StageNav.tsx`, `FilesTab.tsx`, `ReportTab.tsx`, `Gallery.tsx`, `TrainConfigModal.tsx`, `CreateModal.tsx`, `ToolCallCard.tsx`

**Verified:** grepping all of `frontend/src/` for imports of each returns zero matches. These components appear to be remnants from a pre-projects Studio-based architecture. They are still shipped (tree-shaking may prune SOME but not all references if types are exported), confuse new contributors, and drift from the current pattern.

**Fix:** delete them. Or, if anything is load-bearing via dynamic imports or magic, document it at the top of each file.

---

## 🟠 Medium-priority — fix soon

### M-1. Sandbox: `stderr_task` leaked on exception
**File:** `backend/services/sandbox.py:128-157`

If the stdout loop raises (Modal error, user code kills pipe, `CancelledError`), `stderr_task` is orphaned because `await stderr_task` only runs on the happy path.

**Fix:** wrap in `try/finally` and `await`-with-cancel the stderr task.

### M-2. No database indexes on FK / hot columns
**File:** `backend/models.py` (all FK columns)

None of `session_id`, `experiment_id`, `project_id` have `index=True`. Every list-by-session, list-by-experiment, or list-by-project query does a full table scan. On SQLite during dev it's invisible; on Postgres in prod it degrades superlinearly with message/artifact/metric volume.

**Fix:** add `index=True` to the FK columns:
- `messages.session_id`
- `artifacts.session_id`
- `metrics.session_id`, `metrics.project_id` (once Phase C lands), `metrics.training_run_id`
- `processed_dataset_meta.session_id`, `.experiment_id`
- `experiments.project_id`

### M-3. `_known_files` mutation race in execute_code tool
**File:** `backend/tools/execute_code.py:56-59`

Two `execute_code` calls for the same session read the same `_known_files[session_id]`, both compute a diff, both write back. The second write clobbers the first's entries — producing duplicate `file_created` SSE events.

**Fix:** per-session asyncio lock around the read-diff-write, or use `set.difference_update()` on the shared set.

### M-4. Migration runs a destructive wipe on every boot
**File:** `backend/db.py:89-125`

`_run_migrations` has no "already migrated" guard. On every single process start, it counts orphans and potentially executes cascading DELETEs. Previously in `PR_REVIEW.md` this was downgraded as theoretical, but under operational scrutiny: two backends starting simultaneously against the same Postgres both try to wipe. The SQL itself is idempotent-ish, but reasoning about "oops I inserted a test row with NULL project_id" is a landmine.

**Fix:** add a `schema_migrations` table with a version row, or gate the wipe behind "project_id column was just added in this same boot" (the code has the required info; just capture the bool).

### M-5. Whole upload buffered in memory before size check
**File:** `backend/routers/experiments.py:86-100`

`create_experiment` reads 1 MB chunks into `content = b""` and appends. A legal 500 MB upload is fully resident in Python heap before `s3.put_object` streams it out. Concurrent uploads multiply the footprint.

**Fix:** stream directly to S3 via `s3.upload_fileobj(f.file, Bucket, Key)`, or use `s3.create_multipart_upload` for large files.

### M-6. Timestamps stored as `String`, not `DateTime`
**File:** `backend/models.py:34, 35, 67, 68, 104, 105, 143, 207`

Every `created_at`/`updated_at` is `Column(String, ...)` with ISO strings. Lexicographic comparison works for ISO-8601 **only if the timezone representation is identical across rows** (Python's `datetime.isoformat()` produces `+00:00` with `tzinfo=UTC`, but any code path using `utcnow()` without tzinfo will produce naive strings that sort differently).

**Fix:** migrate to `Column(DateTime(timezone=True), ...)`. Cost: one migration step + updating `to_dict()` to call `.isoformat()` on the way out.

### M-7. No `ondelete="CASCADE"` on any FK
**File:** `backend/models.py` (all relationships)

SQLAlchemy's `cascade="all, delete-orphan"` on relationships handles ORM deletes. The FK definitions themselves have no `ondelete=` argument, so **raw SQL deletes** (including the migration wipe at `db.py:89-125`) must manually delete children in order. Any future raw-SQL tool will silently create orphans.

**Fix:** add `ondelete="CASCADE"` to the FK column definitions where it matches the relationship intent.

### M-8. SSE reconnect never drains pending refs
**File:** `frontend/src/app/page.tsx:575`

When EventSource fires `onerror`, `setSseConnected(false)` runs. The browser auto-reconnects silently; `onopen` re-fires and `setSseConnected(true)`. But by then the pending-message / pending-attachment effects have already run with `sseConnected=false` and didn't send. There's no hook on reconnect to re-drain `pendingMessageRef`.

**Fix:** when `sseConnected` transitions false → true AND refs are non-null, trigger the drain. The existing effects depend on `[activeSessionId, sseConnected]`, so if `sseConnected` flips they'll re-fire — BUT if the user cleared the ref on a previous successful drain and set it again later, the effect guard works. The edge case is: ref is set while disconnected → reconnect fires → effect runs once → succeeds. That actually works today because React re-runs the effect when `sseConnected` flips. This is lower confidence than the review suggested; verify with a reconnect test before fixing.

### M-9. SSE URL hardcoded to backend port, bypasses Next.js rewrite
**File:** `frontend/src/lib/sse.ts:3-6` and inline in `page.tsx` via `getSSEBase()`

Both construct `http://${window.location.hostname}:8000` directly. In any deployment where the backend is behind a reverse proxy or not on 8000 from the browser, SSE fails.

**Fix:** use a relative URL `/api/sessions/${id}/stream` so Next.js rewrites handle it (the existing `/api/*` proxy rule does this for regular fetches). Also, `sse.ts` is dead code — `page.tsx` rolls its own EventSource — delete it.

### M-10. Input validation missing on free-text fields
**File:** `backend/schemas.py`

`ExperimentCreate.name`, `ProjectCreate.name`, `ClarificationReply.answer`, and `MessageCreate.content` have no length or content constraints. A 100 MB `content` string will be accepted, stored in `Text`, and replayed into Claude's context (blowing up token usage or hitting SDK limits).

**Fix:** add `Field(max_length=100_000)` (or similar) to each free-text field.

### M-11. ModelSelector leaks a global mousedown listener
**File:** `frontend/src/components/ModelSelector.tsx:18-24`

`document.addEventListener('mousedown', ...)` is registered unconditionally on mount with `[]` deps — never removed. `AgentStatusIndicator` correctly gates this with `if (open)`. ModelSelector doesn't.

**Fix:** match the pattern — only register when `open` is true.

### M-12. `<div onClick>` where `<button>` belongs (a11y)
**File:** `frontend/src/app/page.tsx:2244, 2298` (ToolGroupCard + CollapsibleToolCard headers)

Collapsible card headers are clickable `<div>`s — no keyboard activation, no screen-reader role. Users on keyboard or AT cannot expand tool groups.

**Fix:** `<button type="button">` with `aria-expanded`.

### M-13. S3 presign endpoints accept unvalidated bucket + key params
**File:** `backend/routers/s3_browser.py:65-80, 104-118`

The `/presign` and `/download` endpoints take `bucket` and `key` as query params and pass them straight to `generate_presigned_url`. Any caller can create presigned URLs for **any bucket and key the backend credentials can read**, including internal buckets. This is an auth gap in a single-user app today, but will bite hard the first time this is multi-tenant.

**Fix:** allowlist permitted buckets (`datasets`, `experiments`) and verify the authenticated user owns the object (e.g. key is under `datasets/projects/{pid}/` where they have access).

### M-14. `asyncio.get_event_loop()` deprecation (already fixed in clarifications.py)
**Files:** `backend/services/volume.py:47, 65`

The same pattern still exists in `volume.py`. Python 3.10+ deprecates calling `get_event_loop()` from inside a coroutine.

**Fix:** `asyncio.get_running_loop()`.

### M-15. MCP `is_error` flag dropped (reiteration from PR_REVIEW)
**File:** `backend/services/mcp_tools.py:32-50`

`call_tool` returns a plain `list[TextContent]`, discarding the handler's `is_error: True` flag. Claude reads the error text, so the agent isn't blind, but the SDK-level error hint is lost.

**Fix:** when the handler result dict has `is_error: True`, return `types.CallToolResult(content=[...], isError=True)` instead.

---

## 🟡 Low-priority / tech debt

- **`backend/routers/sessions.py:157-158`** — Dead unreachable `return "eda"` after `return "chat"`.
- **`backend/services/agent/agents.py:16-23`** — `lru_cache(maxsize=None)` on YAML loaders caches exceptions too; a YAML parse error makes that agent permanently broken until restart. Wrap in try/except that only caches successes.
- **`backend/config.py:53`** — Default `cors_origins=["*"]` combined with `allow_credentials=True` in `main.py:60` is rejected by browsers. Default should be the dev origin.
- **`backend/services/s3_client.py, volume.py, sandbox.py`** — Lazy singletons have TOCTOU in `if _foo is None: _foo = ...`. Low impact (boto3/Modal clients are idempotent) but low effort to fix with `@lru_cache`.
- **`backend/config.py:25-26`** — `aws_access_key_id="test"` defaults give no runtime signal when misconfigured; could silently hit a real AWS if `S3_ENDPOINT` points somewhere real.
- **`backend/agents/*.yaml`** — subagent topology has latent cycles (eda ↔ data_prep) currently blocked by `max_depth=1`. Document or add a lint.
- **`backend/requirements.txt`** — lower-bound-only pins on `anthropic`, `claude-agent-sdk`, `modal`, `pydantic`. `claude-agent-sdk` minor versions can silently break the message-shape assumptions in `runner.py`.
- **`backend/Dockerfile:10`** — `npm install -g @anthropic-ai/claude-code` with no version pin.
- **`backend/models.py:205`** — `s3_synced: String(10)` acts as an enum with no CHECK constraint.
- **`backend/services/metadata_extractor.py:168`** — Missing try/except around the upsert; caller catches and logs, but the `metadata_ready` SSE event is never published on failure, so the frontend waits forever.
- **`frontend/src/app/page.tsx:102`** — `meta?: any` on `ChatItem`. A discriminated union keyed on `type` would catch drift.
- **`frontend/src/lib/AppContext.tsx:95, 105`** — `refreshProjects` / `refreshExperiments` swallow errors and return `[]`. UI can't tell "no projects" from "network error".
- **`frontend/src/components/AgentStatusIndicator.tsx, ProjectDataModal.tsx, S3FileBrowserModal.tsx`** — `onClick` inside `<div>` for folder selection and modal close overlay; missing aria attributes; not keyboard-reachable.
- **`frontend/src/app/page.tsx:71-79`** — `getSSEBase` and `getBackendUrl` are the same function duplicated. Delete one.
- **`frontend/src/app/page.tsx:1263-1279, 1370-1385, 1428-1443`** — `key={i}` on dynamic attachment lists where items can be removed.
- **`frontend/src/app/page.tsx:2329-2336`** — `key={i}` on `item.meta.outputs` array where items accumulate.
- **`backend/tests/conftest.py:48-51`** — `scope="session"` event loop fixture is deprecated in pytest-asyncio ≥ 0.22.

---

## ⚪ Claims that did NOT pan out on verification

These were flagged by the scan agents at high/medium priority but don't hold up. Documented here so they don't get re-flagged next round:

| Claim | Reality |
|---|---|
| `.env` with live OAuth token is **committed** to git | `git ls-files .env` returns nothing — it's working-tree only. (It IS still a secret on disk and should stay in `.gitignore`.) |
| `attachMenuRef` collision causes bugs today | The two mounts are in mutually-exclusive ternary branches, so only one node holds the ref at a time. The risk is real if the ternary is flattened, not today. |
| `setActiveProject` stale closure causes wrong experiment state | Every call site follows up with explicit `setActiveExperiment(id, sesId)` that rewrites the state. Observable behavior is correct. |
| Clarifications `_pending` dict leaks timed-out entries | Line 120 pops unconditionally; the comment above it was stale and has been removed. |
| `request_clarification` hangs on `CancelledError` | `CancelledError` ∈ `BaseException`, not `Exception` — it properly escapes `except Exception:` and cancels the whole tree. |
| Pending-refs effects double-fire in Strict Mode | `const x = ref.current; ref.current = null;` is synchronous; second invocation reads null and early-returns. |
| PATCH `/experiments/{id}` changing `project_id` breaks data loading | The runner's `dataset_ref` parameter is declared but unused; data loads via `_load_project_context(project_id)`. Moving a chat correctly picks up the new project's data. |
| `cors_origins=["*"]` with `allow_credentials=True` is exploitable | Exploitable nothing — browsers **reject** this combination per the CORS spec. The bug is "credentialed cross-origin requests silently fail". That's already in Low-priority. |
| Mutable-default column gotcha with `default=list` / `default=dict` | SQLAlchemy calls the callable per row; safe. |

---

## ✅ What's done well

- **`broadcaster.py`** — bounded-queue design (`broadcaster_max_queue_size`, drop-oldest on full) prevents unbounded SSE queue growth. Textbook.
- **`files.py` `_validate_path`** — explicit prefix allowlist + `..` segment check. Correct.
- **`data_explorer.py`** — disables external access before user SQL (`SET enable_external_access = false`). Smart.
- **Clarifications lifecycle** — `cancel_session` drains pending futures AND semaphore entries. Full coverage.
- **`cancelled` flag pattern** in async `useEffect`s — used consistently in `page.tsx` and `ProjectDataModal.tsx`. No unmount state-update leaks.
- **`hydrated` flag in AppContext** — prevents SSR/client mismatch in localStorage-backed state.
- **`metricKeysRef` dedup** — prevents duplicate metric plot points on SSE replay.
- **`lazy="raise"` on relationships** — forces explicit eager-loading decisions, catches N+1 in test rather than prod.
- **`_THOUGHT_BLOCK_MAX_CHARS = 1500`** — sensible cap on agent thought persistence; prevents multi-MB tool outputs bloating the DB.
- **Test coverage** — 11 test files across sessions, experiments, models, broadcaster, S3 sync, validator, data explorer, agent helpers.

---

## Recommended action order

1. **Today, one-hour fixes:** B-2 (dual refs), B-3 (nested setter), B-7 (delete dead components), M-11 (ModelSelector listener), M-12 (a11y buttons), M-14 (volume.py deprecation).
2. **This week:** B-1/B-6 (task registration races), B-4 (S3 pagination), M-1 (stderr task leak), M-3 (execute_code race), M-10 (schema length caps), M-15 (MCP is_error).
3. **Before real load:** M-2 (DB indexes), M-5 (streaming uploads), M-6 (timestamp types), M-7 (ondelete cascade), M-13 (S3 presign authz).
4. **As tech-debt tickets:** all Low-priority items + M-4 (migration one-shot guard), M-8 (SSE reconnect drain), M-9 (proxy-aware SSE).

---

## Summary

- **~60 findings** across three agents, **~30 verified real** after skeptical review.
- **7 high-priority**, **15 medium**, **~15 low/tech-debt**, **8 false positives** (documented above).
- No P0 security vuln in the current threat model (single-user). Multi-tenant deployment would need M-13 (S3 authz) before go-live.
- No architectural rot. The codebase is well-organized for its age; the issues are ordinary wear-and-tear plus a handful of concurrency foot-guns in async Python.
