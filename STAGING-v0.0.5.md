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

## Deferred / not merged

| PR / Issue | Reason |
|-----------|--------|
