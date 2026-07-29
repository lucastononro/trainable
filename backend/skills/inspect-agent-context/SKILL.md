---
name: inspect-agent-context
description: Read another agent's full thought stream from the current session. Returns
when_to_use: Read another agent's thought stream from the same session.
version: '0.1'
kind: capability
---

# inspect-agent-context

Read another agent's full thought stream from the current session. Returns
the agent's text output, tool calls, and tool results in chronological order
as a single string with timestamps. Slice it like a list — pick a `mode` and
pass `offset` + `limit`, or use `head` / `tail` for the first/last N blocks.

Each block in the returned content carries its `created_at` timestamp so you
can compare against your own runtime "Current time" to tell what is recent
vs. stale. Default is `tail=20` — the last 20 thought blocks of the agent.

Use `list_session_agents` first to discover which agent_ids exist in this
session.

## When to use
Read another agent's thought stream from the same session.
