# Trainable ↔ huggingface/ml-intern — feature comparison

**Cloned to:** `/tmp/ml-intern` (commit on `main`, shallow clone, not committed anywhere)
**Compared against:** `/Users/61363/Desktop/trainable` (this repo)
**Date:** 2026-04-27

---

## TL;DR

| Axis | Trainable | ml-intern |
|---|---|---|
| **Surface** | Web studio (Next.js split-pane) + chat | CLI (`prompt_toolkit` REPL) + headless mode + Vite web |
| **Data flow** | Upload CSV → EDA → Prep → Train pipeline | Conversational; agent picks the workflow |
| **Compute** | Modal sandboxes (default + heavy/training profile) | HF Hub Jobs (cpu-basic … a100x8) + ad-hoc HF sandbox |
| **ML scope** | Tabular sklearn / XGB / LGBM / Optuna; **no LLM finetuning** | LLM finetuning (TRL/PEFT/Transformers), HF datasets, paper-to-recipe |
| **Autonomy guardrails** | Hard turn cap = 30 (`backend/config.py:36`) | Token-aware compaction at 90% of model max + 300-iter ceiling + doom-loop detector |
| **Provider** | Claude Agent SDK (locked) | LiteLLM (Claude / OpenAI / HF Router); `/model` swap mid-session |
| **Research** | None — agent guesses configs | `research_tool` sub-agent crawls papers → datasets → code |
| **Observability** | `trainable.log()` → SSE → Recharts | Trackio Space auto-seeded with env vars injected into jobs |
| **Approvals** | Auto (autonomous run) | Per-tool approval w/ `/yolo` to disable |

Bottom line: **Trainable is a polished autonomous studio for tabular ML.** **ml-intern is a research-first, paper-grounded LLM-finetuning agent.** The two are complementary; ml-intern has the strongest answer to *"what hyperparameters should I actually use?"* that Trainable currently lacks.

---

## What each does well

**Trainable's strengths**
- Streaming UI: live notebook cells, live Recharts dashboard, `@`-mention system for artifacts (`frontend/src/app/page.tsx`).
- Clean stage agents (EDA / Prep / Train) with shared session workspace and delegation (`backend/agents/*.yaml`).
- Persistent Jupyter kernels per session — cells remember state (`backend/services/kernel_manager.py`).
- Sandbox profiles: `heavy=true` flag swaps to GPU + extended timeout (`backend/agents/trainer.yaml:38-41`).
- Injected `trainable` SDK in every sandbox: `log()`, `configure_dashboard()`.

**ml-intern's strengths**
- **Paper-first methodology** — system prompt v3 forces "find the paper, read sections 3-5, attribute results to recipes" before code (`agent/tools/research_tool.py:45-173`).
- **Sub-agent isolation** — research runs with separate context + cheaper model (Sonnet 4.6) and its own 170k/190k token budget (`agent/tools/research_tool.py:26-27`).
- **Context compaction** — auto-summarize at 90% of model max, preserving system + first user msg + tail (`agent/context_manager/manager.py:339-401`).
- **Doom-loop detector** — canonicalises tool args, hashes call+result pairs, flags 3 identical-consecutive calls (`agent/core/doom_loop.py`).
- **Slash commands** mid-session: `/model`, `/effort`, `/yolo`, `/compact`, `/undo`, `/status` (`agent/main.py:708-811`).
- **Job pre-flight** — exhaustive hardware ladder with VRAM specs in the tool docstring (`agent/tools/jobs_tool.py:34-61`); reliability check on training scripts before submission (`agent/main.py:443`).
- **Trackio auto-seed** — agent doesn't have to wire metrics manually; env vars are injected into every job (`agent/tools/trackio_seed.py`).
- **Multi-provider via LiteLLM** + per-model effort probing — graceful when a model rejects `xhigh`.

---

## Trainable gaps that ml-intern directly fills

1. **No long-session safety net.** Trainable hard-caps at 30 turns and has no token tracking; long EDA→Prep→Train chains will be hit by this. ml-intern's `ContextManager` is the answer.
2. **No literature grounding.** Trainable's `trainer.yaml` says "tune the best model with optuna (30-50 trials)" but never tells the agent *which* family to bias toward for a given dataset. ml-intern's `research_tool` produces "Recipe → Result → Dataset" tables.
3. **No LLM finetuning.** Trainable's sandbox image lacks `transformers`/`peft`/`trl`. The `heavy=true` GPU profile is wasted on XGBoost.
4. **No mid-session knobs.** Users can't switch model, change reasoning effort, or compact without reloading. ml-intern's slash-command pattern is plug-and-play.
5. **Sub-agent context isn't budgeted.** Trainable allows `max_depth=1` subagents (`trainer.yaml:7`) but they share the parent's context. ml-intern's research sub-agent runs in its own message list.
6. **No doom-loop guard.** A wedged trainer could burn 30 turns repeating the same `execute_code` call. ~120 LOC port.

---

## Recommended ports — ranked

Each row: *what it is* / *ml-intern source* / *Trainable landing spot* / *effort*.

### Tier 1 — high value, low-medium effort

| # | Feature | ml-intern source | Trainable landing spot | Effort |
|---|---|---|---|---|
| 1 | **Token-aware context compaction** (90% threshold, preserves system + first user + tail) | `agent/context_manager/manager.py:133-415` | `backend/services/agent/runner.py` — replace `max_turns=30` cap with token budget tracker; pass per-session into Claude Agent SDK | ~1 day |
| 2 | **Doom-loop detector** (canonicalised args, result-aware hashing) | `agent/core/doom_loop.py` (whole file, ~120 LOC) | `backend/services/agent/runner.py` — wrap each iteration | ~2 hr |
| 3 | **Slash commands in chat input** (`/model`, `/effort`, `/compact`, `/status`, `/undo`) | `agent/main.py:708-811` | `frontend/src/app/page.tsx` chat input + new `routers/sessions.py` endpoints | ~1 day |
| 4 | **Pre-flight job validation** (`check_training_script_save_pattern`) | `agent/main.py:443` + `agent/utils/reliability_checks.py` | New `backend/tools/preflight.py`; call before `execute_code(heavy=true)` | ~half day |
| 5 | **GPU/VRAM ladder baked into tool description** | `agent/tools/jobs_tool.py:33-61` | `backend/tools/execute_code.py` — extend MCP description for the `heavy` flag with the VRAM table; mirror in `trainer.yaml` | ~2 hr |

### Tier 2 — high value, medium-high effort

| # | Feature | ml-intern source | Trainable landing spot | Effort |
|---|---|---|---|---|
| 6 | **Research sub-agent for recipe extraction** (papers → datasets → code, returns ranked recipe table) | `agent/tools/research_tool.py` (entire file, ~480 LOC); `RESEARCH_SYSTEM_PROMPT` is the gem | New `backend/tools/research.py` MCP tool; wire `research` into `agents/trainer.yaml` and `agents/eda.yaml` `tools:` list | ~3-5 days (need HF Papers API access + Semantic Scholar) |
| 7 | **Sub-agent isolated context with own token budget** (research uses Sonnet, capped effort, 60-iter ceiling) | `agent/tools/research_tool.py:220-227,300-356` | `backend/services/agent/runner.py` subagent path — don't inherit parent context for `inspect_agent_context` etc. | ~2 days |
| 8 | **`transformers` + `peft` + `trl` in heavy sandbox profile** + a TRL SFT recipe template | `pyproject.toml` deps + `prompts/system_prompt_v3.yaml` | `backend/services/sandbox.py` training image build; new agent variant `agents/llm_trainer.yaml` | ~2 days |

### Tier 3 — nice-to-have

| # | Feature | ml-intern source | Trainable landing spot | Effort |
|---|---|---|---|---|
| 9 | **Trackio-style auto-seed env vars** for live training dashboards (Trainable has Recharts but agent must call `trainable.log` manually) | `agent/tools/trackio_seed.py` | `backend/services/sandbox.py` — auto-inject `TRAINABLE_RUN_ID` etc. so `trainable.log` works without explicit setup | ~half day |
| 10 | **LiteLLM swap-in** for multi-provider | `agent/main.py` model resolution | `backend/services/agent/runner.py` — sits behind Claude Agent SDK today; not strictly needed | ~3-5 days |
| 11 | **Effort probing** (test if a model accepts `xhigh`/`max` before committing) | `agent/core/effort_probe.py`, `model_switcher.py` | Companion to #3 (`/effort`) | ~half day |
| 12 | **Per-message HF whoami via subprocess+curl** (avoids 40s IPv6 hangs) | `agent/context_manager/manager.py:24-70` | Reuse pattern in any `httpx` call to HF Hub | trivial |

---

## Explicitly NOT recommended

| Feature | Why skip |
|---|---|
| **HF Hub as primary artifact store** | Trainable already uses MinIO/S3 + Postgres; HF Hub should be a *destination* (publish trained model) not a backbone. |
| **Slack/webhook notifications** | Out of scope — Trainable's surface is the studio UI, not async notifications. |
| **`/yolo` auto-approve mode** | Trainable already runs unattended; no per-tool approval flow exists to bypass. |
| **CLI REPL as primary surface** | Trainable's primary surface is the studio; the CLI is just an installer. Keep it that way. |
| **HF Hub session uploader** | Trainable persists sessions in Postgres; no need for HF as a session store. |
| **Trackio Space creation** (vs env-var injection) | Trainable already has Recharts dashboard via `trainable.log`; only the *env-var injection pattern* is worth porting (item #9 above), not Trackio itself. |

---

## Suggested rollout order

1. **Week 1** — items #2 (doom-loop) + #5 (VRAM ladder in tool docstring). Both are pure-text/short-code wins with no infra changes.
2. **Week 1-2** — item #1 (context compaction). Replaces the crude 30-turn cap; unlocks longer EDA→Prep→Train chains.
3. **Week 2** — items #3 (slash commands) + #4 (preflight). User-visible UX wins.
4. **Week 3-4** — item #6 (research tool). Biggest ML-quality win; needs HF Papers + Semantic Scholar wiring.
5. **Week 4+** — item #8 (LLM finetuning lane). Net-new product surface; pair with item #6 — research finds the recipe, the LLM trainer executes it.

---

## Files referenced (clickable)

**Trainable**
- `backend/services/agent/runner.py`
- `backend/services/sandbox.py`
- `backend/agents/trainer.yaml`
- `backend/tools/execute_code.py`
- `backend/config.py:36` (current `agent_max_turns: int = 30`)
- `frontend/src/app/page.tsx`

**ml-intern** (`/tmp/ml-intern`)
- `agent/tools/research_tool.py`
- `agent/context_manager/manager.py`
- `agent/core/doom_loop.py`
- `agent/main.py` (slash commands at L708-811, approval flow at L373-687)
- `agent/tools/jobs_tool.py` (VRAM ladder at L33-61)
- `agent/tools/trackio_seed.py`
- `agent/prompts/system_prompt_v3.yaml`
