"""Human-in-the-loop approval gates (issue #108).

Opt-in, per-session: when the user switches approvals on (a toggle in the
chat input, sent with each run-triggering message), every agent run in the
session gains the `request-approval` capability skill plus a system-prompt
block telling it to post consequential decisions (target column, prep /
leakage plan, model shortlist, expensive runs) as an actionable card and
BLOCK until the user approves or edits.

The blocking future itself is services.clarifications — approvals reuse the
same (session_id, question_id) registry, timeout machinery, and
session-cleanup cancellation. This module only owns the enable flag and the
skill/prompt injection applied by the runner.

Default behavior is byte-identical when the flag is off: `apply_approval_gate`
returns its inputs unchanged.
"""

from __future__ import annotations

APPROVAL_SKILL_SLUG = "request-approval"

APPROVAL_GATE_PROMPT = """## Approval gates (ENABLED for this session)

The user switched on human-in-the-loop approvals. Before COMMITTING to any
consequential decision, call `request-approval` with the decision and block
on the result. Consequential decisions are:

- the prediction **target column** (and problem framing),
- the **data-prep / leakage-handling plan** (drops, imputations, split
  strategy, leakage mitigations),
- the **model shortlist** or final model/hyperparameter-search choice,
- anything that starts a long or expensive run (training sweeps, GPU jobs).

Rules:
- Call `request-approval` BEFORE acting on the decision, never after.
- If the result is `approved`, proceed exactly as proposed.
- If the result is `edited`, the user's revision REPLACES your proposal —
  follow it exactly.
- If the approval times out with no response, proceed with your proposal but
  clearly flag in your report that it was not explicitly approved.
- Don't gate trivial choices (plot styles, file names, obvious defaults) —
  one gate per consequential decision, not per step."""

# Sessions with approval gates switched on. In-memory by design (mirrors the
# clarification futures it gates on): the flag is re-asserted by the frontend
# on every run-triggering message, so a backend restart just falls back to
# the default-off state until the next message.
_enabled_sessions: set[str] = set()


def set_enabled(session_id: str, enabled: bool) -> None:
    """Assert the approval-gate flag for a session (called per run launch)."""
    if enabled:
        _enabled_sessions.add(session_id)
    else:
        _enabled_sessions.discard(session_id)


def is_enabled(session_id: str) -> bool:
    return session_id in _enabled_sessions


def apply_approval_gate(
    session_id: str, agent_skills: list[str], system_prompt: str
) -> tuple[list[str], str]:
    """Inject the approval skill + prompt block when the session opts in.

    When the flag is off this is an identity function — the returned list has
    the same contents and the returned prompt is the same string object, so
    the default path is provably unchanged.
    """
    if not is_enabled(session_id):
        return agent_skills, system_prompt
    skills = (
        agent_skills
        if APPROVAL_SKILL_SLUG in agent_skills
        else [*agent_skills, APPROVAL_SKILL_SLUG]
    )
    return skills, system_prompt + "\n\n" + APPROVAL_GATE_PROMPT
