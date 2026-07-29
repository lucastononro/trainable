---
name: request-clarification
description: Ask your parent agent a clarifying question and pause until you get an
when_to_use: Pause and ask the parent (or user) when the task is ambiguous.
version: '0.1'
kind: capability
---

# request-clarification

Ask your parent agent a clarifying question and pause until you get an
answer. Use this whenever your task is ambiguous, you'd otherwise be making
an assumption that could be wrong, or context from your parent is missing
details you genuinely need. Do NOT proceed on guesses — ask.

Your parent will be invoked briefly to answer. The parent may either reply
directly or escalate the question to the human user. The tool returns
`{ answer, answered_by, question_id }` where `answered_by` is one of
`"parent"`, `"user"`, `"timeout"`, or `"session_ended"`.

Keep `question` short and specific. Use `why_needed` to explain what you'd
do differently depending on the answer.

## When to use
Pause and ask the parent (or user) when the task is ambiguous.
