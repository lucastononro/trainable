---
name: read-project-session
description: Read messages from another chat (session) in the same project. Returns
when_to_use: Read the message history of another chat in this project.
version: '0.1'
kind: capability
---

# read-project-session

Read messages from another chat (session) in the same project. Returns
user prompts, assistant responses, and stage reports by default.

Use this to:
- Recall what the user decided or asked in a previous chat in this project.
- Reuse analysis that was already done elsewhere instead of repeating it.
- Answer questions like "what did we conclude last time?".

Safeguards: only sessions in the current project can be read. Cross-project
reads are rejected. The response is capped at 12k characters — use
`tail`, `offset`, and `limit` to paginate.

Discover available session_ids with `list_project_sessions` first.

## When to use
Read the message history of another chat in this project.
