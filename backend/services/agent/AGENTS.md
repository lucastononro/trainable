# AGENTS.md — backend/services/agent

The agent runner. Orchestrates provider calls, dispatches tool calls, persists messages, publishes events to the frontend.

## Layout

```
runner.py    The core loop — drives a turn from user input to final assistant message
agents.py    Agent YAML loading, system-prompt rendering, skill-list resolution
events.py    Event publishing — post_stage_hook, publish_artifacts, save_and_publish
tasks.py     TaskCreate/TaskUpdate/TaskList tool implementations + silent abort tracking
tools.py     MCP server construction for Claude (services/skills/mcp_bridge.py adapter)
```

## The runner loop (mental model)

```
user message
  │
  ▼
load agent YAML → resolve skill list → render system prompt
  │
  ▼
loop:
  provider.stream(model, system, messages, tools)
    ◄── text events       → save to DB, broadcast to SSE
    ◄── tool_call event   → if Claude (MCP mode): provider handles internally
                            else: dispatch to skill handler, post tool_result, loop
    ◄── usage event       → record_llm_usage, compute cost
    ◄── done              → exit loop
```

Two modes (see `backend/services/llm/AGENTS.md`):
- **Claude/MCP mode**: provider runs its own tool loop; the runner consumes events.
- **Runner-owned loop**: runner dispatches tool calls itself.

## Core principles

1. **The runner is the single coordinator.** Routers do not call providers directly. Services do not call providers directly. Everything that talks to an LLM goes through `runner.run_turn(...)`.
2. **Every observable side effect is broadcast.** Saved a message → publish. Created an artifact → publish. Updated an experiment → publish. The frontend only knows what the broadcaster tells it. See `services/broadcaster.py`.
3. **Persist truncated.** Tool results from `execute-code` can be megabytes. We cap each persisted "thought block" at 1500 chars (`_THOUGHT_BLOCK_MAX_CHARS`). Store enough to debug, not the raw blob. The full result lives on the Modal volume.
4. **Span every turn.** `agent_span()` wraps each call. OTel traces are how we debug "what did the agent actually do."
5. **Mentions are sentinel-delimited.** `<index>` markers in user prompts are stripped by `_apply_mentions` and replaced with a references block. Don't try to render them directly.

## Events and the broadcaster

`events.py` is the single place message-saving + broadcasting are coupled. Functions to use:

| function | when |
| --- | --- |
| `save_and_publish(session, msg)` | Every assistant message — saves to DB and pushes to SSE |
| `publish_artifacts(session, ...)` | When new artifacts (files, plots) land |
| `post_stage_hook(session, stage)` | When an agent transitions stage (eda → prep → train) |

**Never write a message to the DB directly.** Use `save_and_publish`. Direct writes leave the UI stale and confuse the user.

## Subagent delegation

`delegate-task` is a skill, but the runner has special handling:

1. Parent agent calls `delegate-task(target=eda, prompt=...)`.
2. Runner spawns a new turn for `eda` agent with its own message history (or seeded with parent context — see `inspect-agent-context`).
3. Subagent runs to completion (or `max_depth` is exhausted).
4. Final assistant message becomes the `tool_result` returned to the parent.

Rules:
- **`max_depth` is enforced in the runner.** Specialists default to `max_depth: 1`. If you need deeper chains, document why in the agent YAML.
- **Subagent failures propagate as `tool_result is_error=True`.** Parent agent sees the error message and can recover or retry.
- **`_silent_aborts` tracks cancellation.** If the user aborts the session, in-flight subagent calls return a synthetic abort result; the parent sees it as an error and stops.

## Task tracking

`tasks.py` implements the `tasks` skill (a.k.a. TaskCreate/Update/List). Rules:

- **Tasks are per-session.** They don't leak across sessions.
- **The frontend has a dedicated panel for them.** Whenever the agent calls `tasks.create` or `tasks.update`, the frontend should re-render. The broadcaster handles this.
- **Task IDs are stable.** Don't regenerate them — the frontend caches them.
- **Don't reuse the task system for agent-internal state.** If you want to track work the user shouldn't see, use a service-local cache, not tasks.

## Common pitfalls

- **Not capping persisted thought blocks.** A 5 MB `execute-code` stdout will OOM the DB and crash the session viewer. Always truncate before persisting.
- **Bypassing the broadcaster.** Writing a row and then expecting the UI to show it. The UI subscribes to broadcaster events, not DB polls.
- **Spawning a subagent without checking `max_depth`.** Runaway loops have happened. The runner enforces the cap, but if you bypass `delegate-task` and call `run_turn` directly, you can blow past it.
- **Storing OAuth tokens in messages.** OAuth flows redirect through the backend; tokens go in `auth/` storage, never inline in a message.
- **Forgetting to clear log context on exit.** `bind_log_context` adds session/experiment IDs to every log; if you don't `clear_log_context()` after the turn, you'll see them in unrelated logs.

## Streaming behavior

- Text events stream **as the provider emits them**. The frontend renders character-by-character.
- Tool calls are **atomic** — the runner waits for the full tool_call event before dispatching. Partial tool calls are an error.
- Tool results stream their **summary** but persist truncated. The user sees the truncated version in the chat; the full version is on disk.

## Before you ship

- [ ] New events go through `events.py` (no direct DB writes from runner code)
- [ ] New providers handled in both MCP and runner-owned loops
- [ ] `max_depth` respected for any new delegation pattern
- [ ] Truncation applied to anything persisted
- [ ] Span coverage: any new top-level action opens a span
- [ ] Abort path tested — silent aborts should not corrupt parent state
