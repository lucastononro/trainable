---
name: tasks
description: Track multi-step work as a live to-do list visible to the user in the studio.
when_to_use: When the work has 3+ distinct steps OR you're delegating to multiple sub-agents OR the user gave a multi-part request.
version: '0.1'
kind: capability
---

# tasks

Live to-do list for the current session. Modeled on Claude Code's
TaskCreate / TaskUpdate / TaskList — same intent, same lifecycle. The
user sees this list in real time in the studio's Plan tab and can also
add / update / delete tasks themselves.

## When to use

- The work has 3+ distinct steps, OR
- You're delegating to multiple sub-agents, OR
- The user gave a multi-part request ("do X, then Y, then Z").

## When NOT to use

- Single-step or trivial work — just do it.
- Pure conversation / Q&A / explanations.

## Workflow

1. At the start of multi-step work, call `add` once per intended step.
2. Right BEFORE you start work on a step, call `update` with status="in_progress".
3. Right AFTER you finish a step, call `update` with status="completed".
4. Mark steps completed one at a time — never batch-complete at the end.
5. If the plan changes, `add` new steps; don't rewrite history.

## Operations

- `add` — create a new task; returns the task id (use it in subsequent `update` calls).
- `update` — change status / subject / active_form / description.
- `list` — return the current session's full task list (rarely needed; the user already sees it).
