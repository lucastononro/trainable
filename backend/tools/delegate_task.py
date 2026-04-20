"""delegate_task tool — spawns sub-agents with delegation rules."""

from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger(__name__)


def create_handler(
    session_id: str,
    experiment_id: str,
    instructions: str,
    publish_fn,
    parent_agent_type: str,
    current_depth: int = 0,
    gpu: str | None = None,
    parent_model: str | None = None,
    agent_models: dict | None = None,
    parent_agent_id: str = "root",
    parent_parent_agent_id: str | None = None,
    **kwargs,
):
    """Factory: create a delegate_task handler bound to a parent agent context.

    agent_models is a per-agent model override map: {"eda": "claude-haiku-4-5", ...}
    When the parent delegates to a sub-agent, we check this map first.

    parent_agent_id is the agent_id of the agent that owns this tool handler
    (the parent of any delegated child). Stored on synthetic tool_use rows so
    inspectors can attribute the delegation to the right agent.
    """

    agent_models = agent_models or {}

    from services.agent.agents import (
        can_delegate,
        get_agent,
        get_agent_default_model,
        get_agent_subagents,
    )

    allowed_subagents = get_agent_subagents(parent_agent_type)

    async def handler(args: dict):
        agent_type = args.get("agent_type", "")
        task = args.get("task", "")
        context = args.get("context", "")

        # Enforce delegation rules
        if agent_type not in allowed_subagents:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Agent '{parent_agent_type}' cannot delegate to '{agent_type}'. "
                            f"Allowed: {', '.join(allowed_subagents) or 'none'}"
                        ),
                    }
                ],
                "is_error": True,
            }

        try:
            get_agent(agent_type)
        except KeyError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}

        # Enforce max recursion depth
        next_depth = current_depth + 1
        if not can_delegate(parent_agent_type, current_depth):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Max delegation depth reached ({current_depth}).",
                    }
                ],
                "is_error": True,
            }

        # Resolution order: user override > agent YAML default > parent's model
        sub_model = (
            agent_models.get(agent_type)
            or get_agent_default_model(agent_type)
            or parent_model
        )
        stage = "train" if agent_type == "trainer" else agent_type
        child_agent_id = str(uuid.uuid4())[:8]
        synthetic_tool_use_id = f"delegate_{child_agent_id}"

        parent_meta = {
            "agent_id": parent_agent_id,
            "agent_type": parent_agent_type,
            "parent_agent_id": parent_parent_agent_id,
            "depth": current_depth,
        }

        # Persist a synthetic tool_use row attributed to the PARENT before we
        # block on the child. The parent's real tool_use block from the SDK
        # won't be flushed until the parent's turn finishes — and the parent is
        # about to suspend on this very await — so without this row the
        # impersonator call in request_clarification has no record of what was
        # delegated.
        delegation_payload = task[:1000]
        if context:
            delegation_payload += f"\n\n[context]\n{context[:400]}"
        await publish_fn(
            session_id,
            "agent_thought",
            {
                "text": delegation_payload,
                "block_type": "tool_use",
                "tool_name": "delegate_task",
                "tool_use_id": synthetic_tool_use_id,
                "delegated_agent_type": agent_type,
                "delegated_agent_id": child_agent_id,
            },
            role="assistant",
            publish=False,
            agent_meta=parent_meta,
        )

        await publish_fn(
            session_id,
            "subagent_start",
            {
                "agent_type": agent_type,
                "agent_id": child_agent_id,
                "parent_agent_id": parent_agent_id,
                "task": task[:200],
                "model": sub_model,
                "depth": next_depth,
                "parent": parent_agent_type,
            },
            role="system",
            agent_meta=parent_meta,
        )

        start = time.time()
        try:
            # Import here to avoid circular imports
            from services.agent.runner import run_agent

            user_prompt = task
            if context:
                user_prompt = f"{task}\n\n## Context from previous steps\n{context}"

            collected_text = await run_agent(
                session_id=session_id,
                experiment_id=experiment_id,
                stage=stage,
                instructions=instructions,
                user_prompt=user_prompt,
                gpu=gpu,
                model=sub_model,
                agent_type=agent_type,
                depth=next_depth,
                agent_models=agent_models,
                agent_id=child_agent_id,
                parent_agent_id=parent_agent_id,
            )

            duration = round(time.time() - start, 1)

            # Mirror the child's final summary as a tool_result attributed to the parent.
            await publish_fn(
                session_id,
                "agent_thought",
                {
                    "text": (collected_text or "")[:1500],
                    "block_type": "tool_result",
                    "tool_use_id": synthetic_tool_use_id,
                    "tool_name": "delegate_task",
                    "delegated_agent_id": child_agent_id,
                    "duration": duration,
                },
                role="user",
                publish=False,
                agent_meta=parent_meta,
            )

            await publish_fn(
                session_id,
                "subagent_end",
                {
                    "agent_type": agent_type,
                    "agent_id": child_agent_id,
                    "parent_agent_id": parent_agent_id,
                    "duration": duration,
                    "summary": (collected_text or "")[:500],
                },
                role="system",
                agent_meta=parent_meta,
            )

            return {
                "content": [
                    {
                        "type": "text",
                        "text": collected_text
                        or "(agent completed with no text output)",
                    }
                ]
            }

        except Exception as e:
            duration = round(time.time() - start, 1)
            error_msg = f"Sub-agent '{agent_type}' failed after {duration}s: {e}"
            logger.error(error_msg)
            await publish_fn(
                session_id,
                "agent_thought",
                {
                    "text": f"FAILED: {e}",
                    "block_type": "tool_result",
                    "tool_use_id": synthetic_tool_use_id,
                    "tool_name": "delegate_task",
                    "is_error": True,
                    "delegated_agent_id": child_agent_id,
                },
                role="user",
                publish=False,
                agent_meta=parent_meta,
            )
            await publish_fn(
                session_id,
                "subagent_end",
                {
                    "agent_type": agent_type,
                    "agent_id": child_agent_id,
                    "parent_agent_id": parent_agent_id,
                    "duration": duration,
                    "summary": f"FAILED: {e}",
                },
                role="system",
                agent_meta=parent_meta,
            )
            return {"content": [{"type": "text", "text": error_msg}], "is_error": True}

    return handler
