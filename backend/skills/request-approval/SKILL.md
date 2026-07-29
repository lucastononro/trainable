---
name: request-approval
description: >
  Post a consequential decision (target column, prep/leakage plan, model
  shortlist, expensive run) to the user as an actionable approval card and
  BLOCK until they Approve or Edit it.
when_to_use: >
  Only available when the session has approval gates switched on. Call it
  BEFORE committing to a consequential decision — never after acting on it.
version: '0.1'
kind: capability
---

# request-approval

Human-in-the-loop approval gate. Renders an actionable card in the studio
chat with your proposed decision; your run blocks until the user clicks
**Approve** or submits an **Edit** (or the approval window times out).

## Arguments

- `title` — short label for the card, e.g. "Target column" or "Prep plan".
- `decision` — the concrete decision you propose, stated so the user can
  approve it as-is. Markdown allowed.
- `kind` — one of `target_column`, `prep_plan`, `model_shortlist`, `other`.
- `context` (optional) — why you chose this, alternatives you rejected.

## Result contract

- `approved` — proceed exactly as proposed.
- `edited` — the user's revision replaces your proposal; follow it exactly.
- `timeout` — no response within the window; proceed with your proposal but
  flag in your report that it was not explicitly approved.
