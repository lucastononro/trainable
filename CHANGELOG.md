# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.0.5] - 2026-07-29

Consolidation release: 59 PRs/issues merged onto the v0.0.4 line via the
`release/v0.0.5` integration branch. Per-PR merge detail lives in
`STAGING-v0.0.5.md`; fresh verification evidence in
`docs/evidence/v0.0.5-review.md`.

### Added

- Studio product features:
  - Compare leaderboard across experiments (#161)
  - One-click snapshot reproduce / replay (#162)
  - Prediction playground for registered models (#163)
  - Raw dataset preview + column profiling modal (#168)
  - Sample-datasets gallery — create a project from a bundled sample (#169)
  - Per-project training controls (metric, trials, constraints) (#164)
  - Per-project cost budgets with `budget_exceeded` SSE event and fail-open enforcement (#165)
  - Resume stopped sessions with prior-run context (#170)
  - Human-in-the-loop approval gate for sensitive agent actions (#171)
  - EDA action cards — apply findings in prep from the findings panel (#172)
  - Workspace download as zip, with truncation manifest (#87)
  - File-authoring skills: write-file, edit-file, run-file, edit/delete/rerun notebook cell (#85)
- RunPod compute provider (sandboxes, kernels, serving, storage) as an opt-in alternative to Modal (#145)
- Per-project GPU allowance and timeout caps for agent compute (#160)
- CLI: `trainable status`, `trainable logs`, `--version`, and a Docker-daemon preflight check (#126); first CLI test suite (#134)
- Alembic migration framework replacing boot-time DDL, with revisions for `projects.training_config`, `projects.budget_usd`, and `deployments` provider columns (#153, #164, #165, #145)
- Multi-user auth design document (#167, design only — implementation tracked in #113)
- Frontend unit-test infrastructure (vitest + Testing Library) (#133, #158)

### Changed

- Frontend streaming performance and structure:
  - Single session-scoped SSE event bus replacing per-hook EventSources (#148)
  - Memoized chat items with per-item streaming flag (#150)
  - Scroll pinning that survives smooth-scroll animation (#152)
  - Fully typed SSE event union shared by bus, hooks, and type-level tests (#157)
  - Error boundaries around markdown/file viewers with per-file reset (#159)
  - Monolithic `page.tsx` split into ChatPane / WorkspaceCanvas / hooks (#166)
  - react-resizable-panels v2 → v4 API migration with layout persistence (#24)
- Dataset helpers promoted to a public `services/datasets.py` API (#169)
- Sample/preview business logic moved out of routers into services (#168, #169)
- Stale CLI README and issue-tracker links updated (#129, #132)

### Fixed

- S3 uploads: 1 MB chunked streaming with incremental hashing (no full-file buffering), typed upload responses, fail on hash-only uploads (#141, #147)
- boto3 and DuckDB calls offloaded off the event loop; shielded multipart abort on cancellation (#143, #139)
- LLM provider timeouts mapped onto builtin `TimeoutError` so wall-clock and SDK timeouts race cleanly (#142)
- Sentry capture no longer swallows task exceptions; observability test hardening (#151)
- Log-confusion-matrix iterator exhaustion producing silent all-zero matrices (#87)
- RunPod bootstrap deadlock (non-reentrant lock held across ensures), ARG_MAX failure on multi-MB job code, and transient stream-poll errors condemning healthy jobs (#145)
- GPU denial emitted orphaned `tool_end`; `max_timeout` now caps profile-fallback timeouts (#160)
- EDA card keys no longer index-derived (duplicate "Apply in prep" appends) (#172)

### Security

- Opt-in bearer-token auth for `/api/*` (`API_AUTH_TOKEN`), including WebSocket scopes; health/readyz exempt (#138)
- CORS wildcard can no longer be mixed with explicit origins (fail-fast at startup); JSON-array env format parsed explicitly (#140)
- Production compose: secrets required (fails closed without them), datastores bound to 127.0.0.1 (#136)
- CLI writes `.env` with 0600 permissions at creation (TOCTOU-safe `os.open`) (#134)
- S3 bucket allowlist + key validation on the S3 browser API (#143)

### CI & tooling

- Ruff pinned to 0.15.22 across pre-commit, CI, and config (#137, #135)
- Coverage gate on real production coverage (test files excluded) (#149)
- Backend suite runs against Postgres 16 in CI via opt-in `TEST_DATABASE_URL` (#154)
- Advisory vulnerability scans: pip-audit, npm audit, Trivy image scan (#156)
- GitHub Actions major bumps: docker/login-action v4, download-artifact v8 (+ upload-artifact v7), setup-buildx v4, build-push v7 (#7–#10)
- Hashed, universal `backend/requirements.lock` installed with `--require-hashes` in Dockerfile and CI (#173, #128)

### Dependencies

- Backend: fastapi 0.115→0.136.1 (#14), sqlalchemy 2.0.35→2.0.49 (#18), sse-starlette 2.1→3.3.4 (#16), python-multipart 0.0.12→0.0.28 (#19), python-dotenv 1.0.1→1.2.2 (#11)
- Frontend: react-markdown 9→10 (#17), react-syntax-highlighter 15→16 (#13), react-resizable-panels 2→4 (#24), postcss 8.5 (#25), prettier 3.9.6 (#12)
