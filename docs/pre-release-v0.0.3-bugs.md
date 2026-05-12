# BUGS — release/v0.0.3 review tracking

Living catalog of bugs and code-quality issues turned up during the PR #63 review and the audit that followed. One row per finding. Mark **Status** as work lands.

Statuses: `open` · `fixing` · `fixed (<commit>)` · `deferred` · `wontfix`

---

## A. From PR #63 review comments

| # | Location | Finding | Severity | Status |
|---|----------|---------|----------|--------|
| A1 | `backend/db.py:199` | Migration `except Exception` logs at `debug` and continues silently; later code hits "column X does not exist" with no clear cause | high | fixing — bump to `warning`; full alembic migration deferred (A2) |
| A2 | `backend/db.py:200` | Migration block should delegate to alembic | med | deferred — release-blocking work; tracked separately |
| A3 | `backend/requirements.txt:8` | `>=` pins across the file; should be `==` for reproducibility | med | deferred — large, transitive-conflict-risky; needs a dedicated PR |
| A4 | `frontend/src/app/compare/page.tsx` | Orphan page — no sidebar/menu link, only self-references | low | fixing — delete page + api/types entries |
| A5 | `backend/services/llm/factory.py:74` | Broad `except Exception` in `_bootstrap` swallows programming errors (AttributeError, etc.) | med | fixing — narrow to `ImportError` + re-raise others |
| A6 | `backend/services/llm/litellm_provider.py:55` | `groq/llama-3.3-70b-versatile` is a poor catch-all default | low | fixing — switch to `anthropic/claude-sonnet-4-6` |
| A7 | `backend/services/sandbox.py:108` | `_emit` prints user-controlled JSON; can `log_table` row values smuggle a fake `metrics` event? | low (false positive) | wontfix — `json.dumps` escapes newlines, parser is line-anchored and validates a `LOG_EVENT_TYPES` whitelist; safe |
| A8 | `backend/services/sandbox.py:348` | Span context manager opened manually; `finally` is 150 lines below the `try` | low | fixing — refactor to `with` block |
| A9 | `backend/services/canvas.py:1-13` | Docstring spends 8 lines justifying *why this module isn't in metrics* instead of saying what it does | style | fixing — trim docstring + add rule to `AGENTS.md` |
| A10 | `backend/services/agent/runner.py:880` | `asyncio.timeout` wraps the whole loop; non-Claude crash-mid-stream lost partial usage | med | partial fix — verified per-event recording already flushes; deleting dead accumulator helpers that originally caused the concern (B6) |
| A11 | `backend/services/skills/registry.py:165` | `lru_cache(maxsize=1)` makes `discover_skills` global state; hot-reload story unstated | style | deferred — no hot-reload requirement today; add comment naming `reset_cache` as the test hook |
| A12 | `backend/services/skills/state.py:35` | In-process `_code_counter` resets on backend restart; new `step_01_*.py` collides with on-volume files | low | fixing — seed counter from existing files in `/sessions/{sid}/scripts/` on first call |

## B. New findings from the post-review audit

| # | Location | Finding | Severity | Status |
|---|----------|---------|----------|--------|
| B1 | `backend/services/volume.py:168` | `write_to_volume(content: str, ...)` opens temp file in text mode but every caller passes `bytes` from `read_volume_file_async`. Always raises `TypeError`. `register_model_declared` catches it and falls back to the agent's path → **the advertised stable `/projects/{pid}/models/.../v{N}/model.{ext}` registry copy is never created** | high | fixing — accept bytes, pick mode by type |
| B2 | `backend/services/validator.py:396` | Missing `await` on `_read_volume_file_safe(art.path)`. The bare coroutine is truthy → fallback scan skipped → `len(coroutine)` raises `TypeError` → post-train validation silently aborts halfway, caught only by outer `post_stage_hook` | high | fixing — add `await` |
| B3 | `backend/services/agent/runner.py:1058-1131` | `thinking_level` is computed from agent YAML + UI override, then **never plumbed** into `_drive_provider`. The UI thinking-level picker has zero effect on per-call reasoning. `to_provider_config(...)` exists in `services/llm/thinking.py` and is correctly designed for this; just not wired | high | fixing — forward through `_drive_provider`, spread `to_provider_config(...)` into `provider.run(**)`. OpenAI already reads `reasoning_effort`; Claude/Gemini providers don't read thinking kwargs yet (see B4) |
| B4 | `backend/services/llm/claude_provider.py`, `backend/services/llm/gemini_provider.py` | Neither provider accepts `thinking` / `thinking_config` kwargs from `provider.run(...)`. Even with B3 fixed, only OpenAI models actually use the thinking-level setting | med | deferred — needs SDK-specific config translation per provider; follow-up after B3 |
| B5 | `backend/services/agent/events.py:37` | `save_and_publish` broadcasts SSE at line 44 then commits the DB row at line 66. If a frontend re-fetches between, it gets a stale view. Same pattern in `services/canvas.py:53` | low | deferred — small window; SSE payload carries the data so frontend usually doesn't re-fetch |
| B6 | `backend/services/agent/runner.py:863-944` | `_accumulated_usage`, `_USAGE_KEY_ALIASES`, `_normalize_usage`, `_bump_usage`, `_seen_partial_keys`, `_broadcast_partial_llm` are all dead code — defined but never called anywhere in the module | med (maintenance) | fixing — delete; the per-event flush already covers the original intent |
| B7 | `backend/services/registry.py:415-433` | `register_model_declared`'s "best-effort copy" silently masks B1. Every call falls into the `except` branch and pins `artifact_uri` to the agent-supplied path | high | fixed-by-B1 — once B1 lands, this path stops being the de-facto behavior |
| B8 | `backend/services/deploy.py:744-756` | `_ensure_modal_secret` does delete-then-create; brief window where the deployed app rejects every request | low | deferred — acceptable on redeploy, documented |
| B9 | `backend/services/deploy.py:1049-1053` | Secret rotated on Modal before DB commit; commit failure leaves DB and Modal disagreeing on the API key | med | deferred — failure window is small, recoverable by next deploy |
| B10 | `backend/routers/data_explorer.py:62, 152, 206` | `f"... {table_name} ..."` SQL interpolation. Today `table_name` is server-controlled (`train`/`val`/`test`), so not exploitable; pattern is fragile | low | deferred — current callers safe; if dynamic table names ever land, fix first |
| B11 | `backend/db.py:362` | `ALTER TABLE dataset_versions ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'raw'` blanket-tags every pre-existing row. In `release/v0.0.3` the table is new + only raw uploads pre-existed, so this is safe in practice | low | wontfix — semantically correct for the upgrade paths that exist |
| B12 | Skills under `backend/skills/{web-search,inspect-agent-context,read-notebook}` | Uncaught `int(args["limit"])` on agent-supplied args raises `ValueError` straight to the runner instead of a clean validation message | style | deferred — inconsistent across handlers; tracked as a polish pass |

## C. Process / repo-level

| # | Location | Finding | Severity | Status |
|---|----------|---------|----------|--------|
| C1 | `AGENTS.md` | No rule about not-justifying-what-a-module-isn't in docstrings (per A9) | docs | fixing — add to the project-wide pitfalls list |

---

## Rollout

This document evolves with the work. Each fixed row gets the commit SHA appended once it lands. Deferred rows stay here until they're either upgraded to `fixing` or graduated to dedicated issues.

## Validation coverage

Every `fixing`/`fixed` row above has at least one of: a regression unit test, an experiment script under `backend/scripts/bug_validation/`, or both. Mapping:

| Bug | Regression test | Experiment script |
|---|---|---|
| A4 | (none — tsc green) | — |
| A5 | `test_llm_factory.py::test_bootstrap_does_not_swallow_non_import_errors` | `exp_factory_narrow_except.py` |
| A6 | `test_llm_factory.py::test_litellm_default_model_is_claude_sonnet` | — |
| A8 | (style; existing test_drive_provider exercises the new with-block) | `exp_sandbox_span_exit.py` |
| A9 | (style only) | — |
| A12 | `test_skill_registry.py::TestScriptFilenameSeeding` (3 cases) | `exp_step_filename_seeding.py` |
| B1 | `test_volume_write.py` (2 cases) | `exp_write_to_volume_bytes.py` |
| B2 | `test_validator.py::test_validate_train_output_resolves_report_via_artifact_row` | `exp_validator_await.py` |
| B3 | `test_drive_provider.py::test_thinking_level_*` (3 cases) | `exp_thinking_plumbing.py` |
| B6 | (existing suite catches the regression — no new test) | — |

Run all scripts:

```bash
cd backend
for f in scripts/bug_validation/exp_*.py; do
  echo "===== $f ====="
  .venv/bin/python "$f" || { echo "FAILED: $f"; exit 1; }
done
```
