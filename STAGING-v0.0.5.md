# Staging branch `staging-v0.0.5` — merge ledger

Purpose: consolidate all triaged open PRs (as of 2026-07-29) into one integration
branch, off `main` at the v0.0.4 release line, so they can be tested together and
backtracked. Nothing here is merged to `main`.

Base: `origin/main` @ v0.0.4 line.
Method per PR: check outstanding Greptile findings → fix valid ones (pushed to the
PR branch, or noted if dismissed) → fix CI/conflicts → merge `--no-ff` into this
branch → run relevant tests → record below.

## Merged

| Order | PR | Branch | Closes | Greptile follow-ups | Extra fixes | Tests | Notes |
|------|----|--------|--------|--------------------|-------------|-------|-------|
| 1 | #132 | fix/123-issue-tracker-link | #123 | none | none | docs-only | merged clean |
| 2 | #129 | fix/125-update-stale-cli-readme-to-match-v0-0-4- (fork: Dodothereal) | #125 | none | none | docs-only | head branch lives on a fork; merged via `refs/pull/129/head` (head `1659a105` verified as merge parent) |
| 3 | #137 | fix/124-ruff-config | #124 | none | none | `ruff check .` + `ruff format --check .` in `backend/` (ruff 0.15.22 via uvx) — all pass, 142 files formatted | CONTRIBUTING.md auto-merged with #132 (different lines), no conflict |
| 4 | #135 | fix/122-pre-commit-config | #122 | P1 ruff version mismatch pre-commit vs CI — already fixed by author's fixup `3020b76` (pins `ruff==0.15.22` in ci.yml, cross-ref comments) | none | ruff check/format re-run in `backend/` after merge — pass | touches `.github/workflows/ci.yml`; merged clean |
| 5 | #167 | fix/113-multiuser-auth-design | #113 (design note) | 4 outstanding findings fixed on the PR branch in `a18cd6e`: P1 ProjectShare mutual-exclusion → CHECK constraint + create-path validation specified; AuthSession hash-at-lookup pattern made explicit; bootstrap first-come-first-served → env pre-seed recommended, interactive form localhost/token-gated; `scope_to_user` link-browsing exclusion documented as intentional | none | docs-only | greptile comments postdated the branch head, so fixups were applied and pushed to the PR branch before merging |
| 6 | #151 | fix/120-sentry-capture | #120 | P2 bare `except Exception` in test — already fixed by author's fixup `382db8f`; P1 (postdated head) `assert task.done()` after `wait_for` timeout could fail in slow CI — fixed on PR branch in `24f1900` (await task after timeout) | none | full backend suite: 300 passed, 8 skipped | merged clean |
| 7 | #155 | fix/115-agent-sandbox-tests | #115 | both P2 findings (no task-registry cleanup fixture; stale-task cancellation asserted without explicit await) already fixed by author's fixup `befc75f` | none | full backend suite: 315 passed, 8 skipped | test-only; merged clean |
| 8 | #139 | fix/93-offload-duckdb | #93 | both P2 findings (unguarded `raise None` re-raise sites in validator.py) already fixed by author's fixup `6936b64` + regression tests | none | full backend suite: 319 passed, 8 skipped | merged clean |
| 9 | #134 | fix/127-env-file-permissions | #127 | P2 TOCTOU on `.env` creation — fixed by author's fixup `4259bdf` (`os.open` with mode 0600 at creation); P2 "reconfigure skips dir chmod" — verified false positive: `cmd_reconfigure()` delegates to `cmd_init()` which chmods the dir unconditionally | none | new `cli-test` CI job replicated locally: `pytest tests/` in `cli/` — 5 passed | adds first CLI test suite + `cli-test` job in ci.yml; auto-merged ci.yml clean |
| 10 | #136 | fix/90-prod-compose-secrets | #90 | both P2 findings (`.env.example` comment accuracy + stale example values) already fixed by author's fixup `2e313f8` | none | `docker compose -f docker-compose.prod.yml config` fails closed without secrets (`POSTGRES_USER is missing` — intended) and renders OK with dummy env vars | datastores now bound to 127.0.0.1; merged clean |
| 11 | #142 | fix/95-llm-timeout | #95 | all findings already fixed by author's fixup `39929e6`: 2× P1 SDK-timeout vs wall-clock race (OpenAI `APITimeoutError` / `litellm.Timeout` now mapped onto builtin `TimeoutError`) + P2 test coverage for the race | none | full backend suite: 330 passed, 8 skipped | touches `backend/config.py` (later waves beware); merged clean |

## Deferred / not merged

| PR / Issue | Reason |
|-----------|--------|
