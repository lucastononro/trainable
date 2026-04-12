"""request_clarification tool — sub-agent asks parent (or user) for guidance.

Flow:
  1. Sub-agent calls the tool with a question.
  2. We persist the Q under the asker's agent_id.
  3. We run a short, isolated `claude_agent_sdk.query()` impersonating the
     parent — same system prompt, same accumulated thought stream — and ask
     it to either answer or prefix its response with `ESCALATE:` if the
     question requires user input.
  4. If the parent answers directly, we return the answer.
  5. If the parent escalates, we register a future, publish a
     `clarification_request` SSE event, and `await` until the HTTP endpoint
     resolves it (or it times out).
  6. We persist the A under the parent's agent_id so the parent retroactively
     "knows" it answered when it resumes its own turn.
"""

from __future__ import annotations

import asyncio
import logging

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, query
from sqlalchemy import select

from config import settings
from db import async_session
from models import Message
from services.clarifications import get_session_semaphore, register

logger = logging.getLogger(__name__)

_PARENT_CONTEXT_BUDGET = 6000  # chars of parent thought stream to feed the impersonator
_CLARIFICATION_TIMEOUT_S = 120.0
_ESCALATE_PREFIX = "ESCALATE:"


async def _load_parent_thought_stream(session_id: str, parent_agent_id: str) -> str:
    """Pull the parent's recent agent_thought blocks and linearize them."""
    try:
        async with async_session() as db:
            stmt = (
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.id)
            )
            result = await db.execute(stmt)
            rows = list(result.scalars().all())
    except Exception as e:
        logger.error("Failed to load parent context: %s", e)
        return ""

    parts: list[str] = []
    total = 0
    for r in reversed(rows):
        meta = r.metadata_ or {}
        if meta.get("event_type") != "agent_thought":
            continue
        if meta.get("agent_id") != parent_agent_id:
            continue
        block_type = meta.get("block_type", "text")
        tool_name = meta.get("tool_name") or ""
        header = f"[{r.created_at}] [{block_type}{':' + tool_name if tool_name else ''}]"
        chunk = f"{header}\n{r.content or ''}"
        if total + len(chunk) > _PARENT_CONTEXT_BUDGET:
            break
        parts.append(chunk)
        total += len(chunk)
    parts.reverse()
    return "\n\n".join(parts)


async def _run_impersonator(
    parent_agent_type: str,
    parent_thought_stream: str,
    question: str,
    why_needed: str,
    asker_agent_type: str,
    parent_model: str | None,
) -> str:
    """One-shot Claude call that impersonates the parent to answer one question.

    No tools — pure text in, pure text out.
    """
    from services.agent.agents import (
        get_agent_default_model,
        render_agent_system_prompt,
    )

    parent_system = render_agent_system_prompt(
        parent_agent_type,
        experiment_id="",
        session_id="",
        instructions="",
        prev_context="(see thought stream below)",
    )

    impersonator_prompt = (
        f"{parent_system}\n\n"
        f"## Your recent thought stream (for context)\n"
        f"{parent_thought_stream or '(empty)'}\n\n"
        f"## Clarifying question from your sub-agent\n"
        f"Sub-agent type: {asker_agent_type}\n"
        f"Question: {question}\n"
        f"Why they need it: {why_needed or '(not specified)'}\n\n"
        f"## Your task\n"
        f"Answer the question concisely (1–3 sentences) so your sub-agent can resume.\n"
        f"If — and only if — you genuinely cannot answer without input from the human "
        f"user, respond with the literal token `{_ESCALATE_PREFIX}` followed by a "
        f"user-friendly version of the question. Do not escalate questions you can "
        f"answer yourself."
    )

    model = parent_model or get_agent_default_model(parent_agent_type) or settings.claude_model

    options = ClaudeAgentOptions(
        system_prompt="You are answering a brief clarifying question for one of your sub-agents.",
        model=model,
        permission_mode="bypassPermissions",
        max_turns=1,
        tools=[],
        allowed_tools=[],
        env={"CLAUDE_CODE_OAUTH_TOKEN": settings.claude_code_oauth_token},
    )

    collected = ""
    try:
        async with asyncio.timeout(60):
            async for message in query(prompt=impersonator_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text") and block.text:
                            collected += block.text
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("Impersonator call timed out")
        return f"{_ESCALATE_PREFIX} {question}"
    except Exception as e:
        logger.exception("Impersonator call failed")
        return f"{_ESCALATE_PREFIX} {question} (parent could not answer: {e})"

    return collected.strip()


def create_handler(
    session_id: str,
    publish_fn,
    parent_agent_type: str,
    parent_agent_id: str = "root",
    parent_parent_agent_id: str | None = None,
    current_depth: int = 0,
    parent_model: str | None = None,
    **kwargs,
):
    """Bind a clarification handler. The 'parent_agent_id' here is the agent_id
    of the agent that owns this tool — i.e. the agent the asker will talk to.

    Note: in the typical case, an agent calls request_clarification on its OWN
    parent. Since the tool is bound at MCP server creation time, parent_agent_id
    here is actually the agent_id of the agent that will use the tool. The
    impersonator answer should come from that agent's own parent — but since
    the parent is suspended waiting on us, we instead impersonate the calling
    agent's PARENT (parent_parent_agent_id) when we have it; otherwise we
    impersonate the agent itself as a self-reflection fallback.
    """

    asker_agent_type = parent_agent_type  # the agent that holds this tool
    asker_agent_id = parent_agent_id

    # The agent we impersonate to "answer" is the asker's parent if we have it,
    # otherwise the asker itself (self-reflection / forces escalation).
    answerer_agent_id = parent_parent_agent_id or asker_agent_id

    asker_meta = {
        "agent_id": asker_agent_id,
        "agent_type": asker_agent_type,
        "parent_agent_id": parent_parent_agent_id,
        "depth": current_depth,
    }

    async def handler(args: dict):
        question = (args.get("question") or "").strip()
        why_needed = (args.get("why_needed") or "").strip()
        urgency = args.get("urgency") or "normal"

        if not question:
            return {
                "content": [{"type": "text", "text": "question is required"}],
                "is_error": True,
            }

        # 1) Persist the question under the asker.
        await publish_fn(
            session_id,
            "clarification_q",
            {
                "text": question,
                "why_needed": why_needed,
                "urgency": urgency,
                "asker_agent_id": asker_agent_id,
                "answerer_agent_id": answerer_agent_id,
            },
            role="assistant",
            agent_meta=asker_meta,
        )

        # 2) Run the impersonator under a per-session semaphore.
        sem = get_session_semaphore(session_id)
        try:
            async with sem:
                parent_stream = await _load_parent_thought_stream(
                    session_id, answerer_agent_id
                )
                raw_answer = await _run_impersonator(
                    parent_agent_type=asker_agent_type,
                    parent_thought_stream=parent_stream,
                    question=question,
                    why_needed=why_needed,
                    asker_agent_type=asker_agent_type,
                    parent_model=parent_model,
                )
        except Exception as e:
            logger.exception("request_clarification: impersonator wrapper failed")
            raw_answer = f"{_ESCALATE_PREFIX} {question} (impersonator error: {e})"

        # 3) Branch: direct answer vs escalation.
        if raw_answer.lstrip().startswith(_ESCALATE_PREFIX):
            user_question = raw_answer.lstrip()[len(_ESCALATE_PREFIX):].strip() or question

            question_id, future = register(
                session_id=session_id,
                asker_agent_id=asker_agent_id,
                parent_agent_id=answerer_agent_id,
                question=user_question,
                timeout_s=_CLARIFICATION_TIMEOUT_S,
            )

            await publish_fn(
                session_id,
                "clarification_request",
                {
                    "question_id": question_id,
                    "question": user_question,
                    "original_question": question,
                    "why_needed": why_needed,
                    "urgency": urgency,
                    "asker_agent_id": asker_agent_id,
                    "asker_agent_type": asker_agent_type,
                    "answerer_agent_id": answerer_agent_id,
                    "depth": current_depth,
                },
                role="system",
            )

            try:
                payload = await future
            except asyncio.CancelledError:
                payload = {
                    "answer": "(clarification cancelled)",
                    "answered_by": "session_ended",
                    "timeout": False,
                }

            answered_by = payload.get("answered_by", "user")
            answer_text = payload.get("answer", "(no answer)")

            # Record the answer attributed to the answerer (so it surfaces
            # on the parent's next turn via _load_conversation_history).
            answerer_meta = {
                "agent_id": answerer_agent_id,
                "agent_type": parent_agent_type,
                "parent_agent_id": None,
                "depth": max(0, current_depth - 1),
            }
            await publish_fn(
                session_id,
                "clarification_a",
                {
                    "text": answer_text,
                    "question_id": question_id,
                    "answered_by": answered_by,
                    "asker_agent_id": asker_agent_id,
                    "answerer_agent_id": answerer_agent_id,
                },
                role="assistant",
                agent_meta=answerer_meta,
            )

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"answered_by={answered_by} question_id={question_id}\n"
                        f"answer: {answer_text}"
                    ),
                }]
            }

        # Direct parent answer.
        answerer_meta = {
            "agent_id": answerer_agent_id,
            "agent_type": parent_agent_type,
            "parent_agent_id": None,
            "depth": max(0, current_depth - 1),
        }
        await publish_fn(
            session_id,
            "clarification_a",
            {
                "text": raw_answer,
                "answered_by": "parent",
                "asker_agent_id": asker_agent_id,
                "answerer_agent_id": answerer_agent_id,
            },
            role="assistant",
            agent_meta=answerer_meta,
        )

        return {
            "content": [{
                "type": "text",
                "text": f"answered_by=parent\nanswer: {raw_answer}",
            }]
        }

    return handler
