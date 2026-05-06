---
name: list-session-files
description: List files in another session's workspace (same project only). Use this
when_to_use: List files in a sibling chat's workspace within the same project.
version: '0.1'
kind: capability
---

# list-session-files

List files in another session's workspace (same project only). Use this
before `read_session_file` to discover what's available to pull — reports,
scripts, figures, parquet samples, or trained model files produced in a
sibling chat.

Safeguards: only sessions in the current project are accessible. Output is
capped at 200 files; pass a `glob` like "*.md" or "models/*.pkl" to narrow.

Discover session_ids with `list_project_sessions` first.

## When to use
List files in a sibling chat's workspace within the same project.
