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

## Deferred / not merged

| PR / Issue | Reason |
|-----------|--------|
