---
name: read-session-file
description: Read a single file from another session's workspace (same project only).
when_to_use: Pull a single file from another session's workspace.
version: '0.1'
kind: capability
---

# read-session-file

Read a single file from another session's workspace (same project only).
Text files (.md, .py, .json, .csv, .txt, etc.) come back as UTF-8 text
with offset/limit pagination. Binary files (images, parquet, pickles)
come back base64-encoded and capped at ~200KB.

Use after `list_session_files` to pull a specific report, script, or
figure from a sibling chat without re-running the work.

Safeguards: only sessions in the current project; path traversal blocked.

## When to use
Pull a single file from another session's workspace.
