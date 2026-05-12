# AGENTS.md — root

> Read this first. Folder-specific AGENTS.md files inherit these rules.

## What this repo is

Trainable is an **AI-powered ML experimentation platform**. A user uploads a dataset, an orchestrator agent plans the workflow, specialist sub-agents (EDA, prep, trainer, deploy) run Python in Modal sandboxes, and the user watches live metrics + artifacts stream into a split-pane UI.

```
frontend (Next.js)  ─ SSE ─►  backend (FastAPI)  ──►  Modal sandboxes
                                   │
                                   ├── agents/      YAML declarations
                                   ├── skills/      tool implementations
                                   └── services/llm  multi-provider abstraction
```

## Core principles (in priority order)

1. **Ship narrow, complete vertical slices.** A PR that touches one feature end-to-end (router + service + skill + frontend + test) is better than a PR that scaffolds five features halfway. Big landing PRs have caused most of our regressions and rebases.
2. **Verify before declaring done.** Backend changes: run the test that exercises the path. Frontend changes: load the page in a browser and click through. UI changes: compare against the reference screenshot in the issue. "It compiles" is not done.
3. **Defaults are contracts.** Where files are written (cwd vs `~/.trainable/`), what a tool is named (`trainable` vs `trainable-ai`), where a worktree lives (`../trainable-foo` vs `.claude/...`) — these defaults are user-facing and stable. Don't pick one casually.
4. **Be explicit about the deny path.** Access controls, validation, error handling: write the failure case before the happy path. *"If project A reads from project B's storage, raise `PermissionError`"* belongs in the design, not in a follow-up.
5. **One source of truth.** Models, pricing, agent definitions, skill registrations → YAML files committed to the repo. Never duplicate them as Python constants. The runner reads YAML; the UI fetches the same data.
6. **The user-facing surface uses names, not IDs.** Internal: UUID. Logs: UUID. Frontend display: human-readable name. Never leak `experiment_id` into a label.

## Repo map

| Folder | What lives here | Read its AGENTS.md before changing |
| --- | --- | --- |
| `backend/` | FastAPI app, models, routers, services, skills, tests | yes |
| `backend/agents/` | YAML agent declarations (chat, orchestrator, eda, ...) | yes |
| `backend/skills/` | One folder per skill: `SKILL.md` + `schema.yaml` + `handler.py` | yes |
| `backend/services/llm/` | Provider Protocol, `LLMEvent`, Claude/OpenAI/Gemini providers | yes |
| `backend/services/agent/` | Runner loop, event publishing, task tracking | yes |
| `backend/routers/` | One router module per resource (experiments, models, sessions, ...) | yes |
| `frontend/` | Next.js 14 app router, AppContext, lucide-react, dagre/xyflow | yes |
| `frontend/src/components/` | React components — sidebar, modals, canvas, lineage graph | yes |
| `frontend/src/lib/` | AppContext, API client, types, mention parsing | yes |
| `cli/` | `trainable-ai` PyPI package: `trainable init` wizard, compose orchestration | yes |
| `docs/` | C1-C4 architecture diagrams, demo scripts | no |
| `sample-data/` | Example datasets used by the gallery | no |

## How to contribute (PR workflow)

1. **Branch from `main`** (or the current release branch if a release is in flight). Branch name uses `feat/`, `fix/`, `refactor/`, `docs/`, `test/`, `ci/` prefixes.
2. **Open an issue first** for anything beyond a small fix. The issue is where scope gets pinned; the PR is where code lands. We've burned weeks on PRs whose scope drifted because no issue anchored it.
3. **Keep PRs reviewable.** Soft cap: <500 lines diff, single concern. If you're at 1000+ lines or touching unrelated areas, split.
4. **Commit message uses conventional commits** (`feat:`, `fix:`, `refactor:`, etc.). The body explains the *why*, not the *what*.
5. **Run the checks locally before pushing**: `ruff check backend/`, `cd backend && pytest`, `cd frontend && npm run build && npm run lint`. CI runs them too, but a 10-second local run beats a 5-minute CI loop.
6. **Update the relevant AGENTS.md** if your change alters a convention this document promises.

## Project-wide pitfalls (learned the hard way)

- **Don't commit audit/review markdown to repo root.** Files like `CODEBASE_AUDIT.md`, `PR_REVIEW.md` belong in PR descriptions or `docs/`, not the working tree.
- **Don't write generated artifacts into cwd.** Compose files, secrets, generated `app.py`s belong under `~/.trainable/` (CLI artifacts) or the Modal volume (runtime artifacts).
- **Don't introduce a config table as a Python constant.** Use YAML (`models.yml`, `sandbox.yml`, agent YAMLs). Constants are unparseable by the UI and force a code change for what should be a data change.
- **Don't paste large stack traces into the codebase as "fixed by".** If you hit an asyncpg/SQLAlchemy or `.claude.json` warning, file a root-cause ticket. Log noise that recurs is a symptom, not background.
- **Don't ship multi-provider scaffolding that doesn't work end-to-end.** If only Claude works today, the OpenAI/Gemini providers should either work or not be in the model picker. Half-wired providers confuse users and waste their API keys.
- **Don't trust `gh pr edit` silently.** After updating a PR body, fetch it back and diff. Several silently-failed edits cost us real time.
- **Don't squash legitimate features into a single mega-commit.** OTel + provider factory + UI polish in one 5000-line PR makes bisecting regressions impossible.
- **Module docstrings describe what the module *is*, not what it isn't.** Skip the "kept separate from X so they don't tangle" justification — the file path already says where the code lives. Lead with the responsibility (e.g. "Canvas HTML/JS artifact publishing.") and leave architectural rationale for the PR description or AGENTS.md.

## Working with the codebase as an AI agent

These rules apply if *you* are an LLM contributing to this repo (Claude Code, Cursor, etc.):

- **Restate multi-item asks as a numbered checklist** before writing code. If the user says "add X, also Y, also Z", produce a checklist back and confirm before starting.
- **For UI work, open the running frontend and click through** before saying done. The chrome MCP / playwright / a manual screenshot — pick one. Don't claim a page is fixed because the code compiled.
- **For "how does X work here?" questions, grep first.** Then cite `file:line` in the answer. Don't answer from general training knowledge — this codebase has its own model and you will be wrong.
- **For "rebuild / redesign" requests, write a one-paragraph design memo first** and get explicit ack. The default tendency to do a literal port has caused multiple full-rebuilds.
- **Default new files to where they belong** (use the repo map above). Worktrees → sibling directory. Generated CLI artifacts → `~/.trainable/`. Audit notes → never in the repo.
- **For long sessions, write a `WORKING_NOTES.md` and ask the user to seed it on resume.** Context compaction will wipe load-bearing mental models otherwise.

## Tech stack reference

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy (async), PostgreSQL (prod) / SQLite (dev), Modal SDK for sandboxes, OpenTelemetry for traces.
- **LLM providers**: Anthropic SDK (Claude, primary), OpenAI SDK (Responses API — *not* Completions), google-genai (Gemini), LiteLLM (fallback wrapper). All conform to `LLMProvider` protocol in `backend/services/llm/base.py`.
- **Frontend**: Next.js 14 (app router), React 18, TypeScript strict, lucide-react, @xyflow/react + @dagrejs/dagre (lineage canvas), prismjs (code highlighting).
- **Storage**: MinIO (dev) / S3 (prod) for artifacts. Modal Volume for sandbox-mounted files at `/data`.
- **Streaming**: SSE from `/stream` endpoints. Broadcaster fan-out per session.

## When in doubt

- "Where does this go?" → Look at an adjacent file in the same folder and follow its shape.
- "Should I add a new file?" → Almost always no. Edit the existing one.
- "Should this be one PR or three?" → Three.
- "Should I name it `foo` or `foo_v2`?" → `foo`. Delete the old one.
- "Is this constraint worth stating up front?" → Yes.
