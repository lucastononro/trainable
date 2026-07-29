"""request-approval skill — HITL approval gate on consequential decisions.

Flow:
  1. Agent calls the skill with its proposed decision.
  2. We register a blocking future in services.clarifications (same registry,
     timeout, and session-cleanup semantics as request-clarification) and
     publish an `approval_request` SSE event that renders the actionable card.
  3. The run BLOCKS on the future until the user clicks Approve / submits an
     Edit via POST /sessions/{id}/approvals/{approval_id} — or the window
     times out.
  4. We publish `approval_resolved` (persisted, so reloads restore the card's
     final state) and return the outcome as the tool result.
"""

from __future__ import annotations

import asyncio
import logging

from services.clarifications import register

logger = logging.getLogger(__name__)

# Approval gates exist to stop expensive work from launching unreviewed, so
# the window is generous. On timeout the agent is told to proceed with its
# proposal but flag that it wasn't explicitly approved.
_APPROVAL_TIMEOUT_S = 1800.0

_VALID_KINDS = {"target_column", "prep_plan", "model_shortlist", "other"}


def create_handler(
    session_id: str,
    publish_fn,
    parent_agent_type: str,
    parent_agent_id: str = "root",
    parent_parent_agent_id: str | None = None,
    current_depth: int = 0,
    **kwargs,
):
    asker_meta = {
        "agent_id": parent_agent_id,
        "agent_type": parent_agent_type,
        "parent_agent_id": parent_parent_agent_id,
        "depth": current_depth,
    }

    async def handler(args: dict):
        title = (args.get("title") or "").strip()
        decision = (args.get("decision") or "").strip()
        context = (args.get("context") or "").strip()
        kind = args.get("kind") or "other"
        if kind not in _VALID_KINDS:
            kind = "other"

        if not title or not decision:
            return {
                "content": [
                    {"type": "text", "text": "title and decision are required"}
                ],
                "is_error": True,
            }

        approval_id, future = register(
            session_id=session_id,
            asker_agent_id=parent_agent_id,
            parent_agent_id=parent_parent_agent_id,
            question=f"[approval:{kind}] {title}: {decision}",
            timeout_s=_APPROVAL_TIMEOUT_S,
        )

        # `content` carries the decision text so the persisted Message row
        # (and the restore-on-reload path) has the card body; everything else
        # lands in metadata.
        await publish_fn(
            session_id,
            "approval_request",
            {
                "content": decision,
                "approval_id": approval_id,
                "title": title,
                "kind": kind,
                "context": context,
                "asker_agent_id": parent_agent_id,
                "asker_agent_type": parent_agent_type,
                "depth": current_depth,
            },
            role="system",
            agent_meta=asker_meta,
        )

        try:
            payload = await future
        except asyncio.CancelledError:
            payload = {
                "decision": "cancelled",
                "answer": "(session ended before the approval was answered)",
                "answered_by": "session_ended",
                "timeout": False,
            }

        answer_text = (payload.get("answer") or "").strip()
        outcome = payload.get("decision")
        if outcome is None:
            # Resolved through a path that didn't set an explicit decision
            # (timeout, or the generic clarification endpoint): a timeout is
            # a timeout; any real free-text reply is treated as an edit.
            outcome = "timeout" if payload.get("timeout") else "edit"

        await publish_fn(
            session_id,
            "approval_resolved",
            {
                "approval_id": approval_id,
                "decision": outcome,
                "answer": answer_text,
                "answered_by": payload.get("answered_by", "user"),
            },
            role="system",
            agent_meta=asker_meta,
        )

        if outcome == "approve":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"APPROVED (approval_id={approval_id}) — the user "
                            "approved this decision. Proceed exactly as proposed."
                        ),
                    }
                ]
            }
        if outcome == "edit":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"EDITED (approval_id={approval_id}) — the user "
                            "revised the decision. Their revision REPLACES your "
                            f"proposal; follow it exactly:\n{answer_text}"
                        ),
                    }
                ]
            }
        if outcome == "cancelled":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"CANCELLED (approval_id={approval_id}) — {answer_text}",
                    }
                ],
                "is_error": True,
            }
        # timeout
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"NO RESPONSE (approval_id={approval_id}) — the approval "
                        "window elapsed without an answer. Proceed with your "
                        "proposed decision, and clearly flag in your report that "
                        "it was not explicitly approved."
                    ),
                }
            ]
        }

    return handler
