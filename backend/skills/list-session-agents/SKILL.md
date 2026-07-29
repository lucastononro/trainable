---
name: list-session-agents
description: List every agent that has produced messages in the current session, with
when_to_use: Discover which agent_ids exist in this session before inspecting them.
version: '0.1'
kind: capability
---

# list-session-agents

List every agent that has produced messages in the current session, with
their agent_id, agent_type, parent_agent_id, depth, block count, and the
timestamps of their first and last activity. Use this to discover which
agent_ids you can pass to `inspect_agent_context`.

## When to use
Discover which agent_ids exist in this session before inspecting them.
