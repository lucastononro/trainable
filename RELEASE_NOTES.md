# Release notes

## v0.0.3 — 2026-05-12

This release turns Trainable from a Claude-only prototype into a multi-provider, skill-driven agent studio with first-class lineage, deploy, and live observability.

### Highlights

- **Multi-provider LLM stack.** A new `LLMProvider` protocol with normalized `LLMEvent` types (text / tool_call / tool_result / usage / error / done) and a lazy factory that registers each provider only when its SDK + auth resolve. Backends: `claude_provider` (production path, MCP-aware, OAuth-aware usage accounting for Claude Pro/Max subscriptions), `openai_provider`, `gemini_provider`, `litellm_provider`. Per-provider auth resolvers (`auth/{claude,openai,gemini,litellm}.py`) consistently prefer OAuth → API key.
- **Skills system.** 28 capability skills under `backend/skills/<name>/{SKILL.md, schema.yaml, handler.py}` — execute-code, append-notebook-cell, run-notebook-cell, read-notebook, papers-search, web-search, eda-report, train-tabular, register-dataset / register-raw-dataset / register-model, fork-experiment, create-experiment, create-serving-app, validate-serving-app, delegate-task, inspect-agent-context, tasks, use-skill, request-clarification, show-html, list-*/read-* helpers, etc. Skills are discovered, MCP-bridged, and tracked per-session by `services/skills/{registry,mcp_bridge,state,visible_events}.py`.
- **Live agent tasks.** Agents now maintain a multi-step to-do list rendered inline in the studio at each interaction, hydrated and streamed via SSE. The orchestrator, chat, and trainer agents are nudged to plan with the `tasks` skill.
- **Search agents.** `papers-search` + `web-search` skills, plus a `researcher` agent that mandates a `references/` folder for every cited paper. Replaces the old separate research / data_search agents.
- **Deploy pipeline.** Typed Pydantic contract surfaces real schemas in Swagger `/docs`; `validate-serving-app` runs as a pre-flight skill; framework-aware loader avoids the previous hang; auto X-API-Key auth via Modal secrets (show / copy / rotate); inspect+edit `app.py` panel with a syntax-highlighted Python editor; generous default deps for new apps.
- **Lineage graph.** New `frontend/src/components/lineage/` ReactFlow + dagre layout with role-coloured edges (dataset / experiment / model nodes) and a slide-in metadata panel. Backed by `services/lineage.py` + `routers/lineage.py`.
- **Canvas overhaul.** Auto-opens on `file_created` / `files_ready` / report / metrics / notebook events; picks a sensible default tab on cold open; tab state persists across switches; heavy tabs (report markdown, metrics) memoized so hidden tabs skip reconciliation. New `show-html` skill renders arbitrary HTML/JS artifacts (Plotly, custom dashboards) as canvas tabs.
- **Rich metrics.** Chart-first layout with a compact summary on by default (preference persisted). Log payloads now accept images, tables, and confusion matrices.
- **Session workspace as Python repo.** The per-session workspace is now an importable Python repo — handlers/notebooks can `from <workspace>.foo import bar` instead of juggling `sys.path`.
- **Sample data.** License-plates object-detection demo (downloader, S3 uploader, 3 sample images + COCO annotations) for quick CV testing.

### Backend

- `services/llm/**` — provider protocol, four backends, auth resolvers, `thinking.py` for cross-vendor reasoning-level translation, `models.yml` catalogue.
- `services/agent/{agents,runner,events,tasks,tools}.py` + `agents/*.yaml` — provider-agnostic runner with MCP-aware Claude path and manual tool loop for everyone else; per-role YAML personas (chat, orchestrator, trainer, researcher, data_prep, eda, feature_eng, deploy, reviewer).
- `services/{sandbox,kernel_manager,volume,snapshot,canvas,validator}.py` — Modal sandbox + persistent kernel + reproducibility snapshots + bulk Volume upload + HTML canvas artifacts.
- `services/{lineage,registry,dataset_versions,experiments,deploy,metrics,usage}.py` — lineage graph builder, model/dataset registry, experiment lifecycle, usage metering.
- `routers/{lineage,registry,experiments,projects,sessions,snapshots,compare,models,files,usage,skills}.py` — REST surface for everything above.
- `observability.py` — OTel + Sentry telemetry wiring on FastAPI bootstrap.
- `db.py` — lineage migrations + cascade-delete deployments when projects/experiments are removed.

### Frontend

- `app/projects/[id]/lineage/page.tsx` + `components/lineage/*` — the new lineage view.
- `app/{compare,experiments,models,usage}/page.tsx` — compare, experiments table, models list, usage dashboard.
- `components/{InlineTasks,MetricsTab,ModelSelector,Sidebar,AgentStatusIndicator,CostBadge,SearchResults,PythonCodeEditor,ProjectDataModal}.tsx` — task list, metrics charts, backend-driven model picker, sidebar w/ live run indicator, cross-project search, Monaco-based editor.
- `lib/{AppContext,api,types}.ts` — typed API client, app-wide context.

### CLI

- `trainable init` wizard rewritten: multi-provider auth picker, existing-config merge instead of clobber, LiteLLM free-form keys, Docker check, launch.

### Infra / dev

- `docker-compose.{yml,prod.yml}` updated for the multi-provider env shape.
- `.github/workflows/ci.yml` now triggers on `release/*` branches.
- `.env.example` rewritten — covers all four providers + Modal + telemetry.
- 23 new backend tests covering providers, auth resolvers, MCP bridge, skill registry, lineage routes, usage, experiment lifecycle, sandbox session-repo bootstrap.

### Bug fixes

- Chat: close orphan tool/subagent spinners when a run terminates.
- Tools: persist `duration` on `tool_end` so reloaded sessions show real elapsed time.
- Sidebar: light up chat-row spinner the moment a run starts.
- Header: derive experiment name from context so rename updates instantly.
- Canvas: only auto-open on `file_created` (not every event); win the cold-open race on mount.
- Workspace: render PDFs in an iframe instead of dumping bytes.
- DB: cascade-delete deployments to avoid orphaned rows.

### Upgrade notes

- **Env vars.** New release pulls more provider-specific variables. Start from the new `.env.example` and merge in your existing secrets (or re-run `trainable init`, which now merges instead of clobbering).
- **Migrations.** Lineage tables are added at startup via `db.py` migration block — run once on first boot.
- **Existing sessions.** The workspace-as-Python-repo convention changes import paths for handlers running inside the sandbox. New sessions pick this up automatically; long-lived sessions may want a kernel restart.

---

## v0.0.2

Pre-release. No notes archived.

## v0.0.1

Initial release.
