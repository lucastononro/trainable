"""Core agent loop — orchestrates Claude Agent SDK calls."""

from __future__ import annotations

import asyncio
import logging

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    query,
)
from sqlalchemy import select

from config import settings
from db import async_session
from models import Message
from services.volume import read_volume_file

from .agents import (
    get_agent_default_model,
    get_agent_opener,
    get_agent_tools,
    render_agent_system_prompt,
)
from services.broadcaster import broadcaster

from .events import post_stage_hook, publish_artifacts, save_and_publish
from .tasks import _silent_aborts
from .tools import create_mcp_server

logger = logging.getLogger(__name__)

_TOKEN_CHUNK_SIZE = 12
_TOKEN_DELAY = 0.012  # seconds between chunks


async def _stream_text(session_id: str, text: str):
    """Publish text as small agent_token chunks to simulate streaming."""
    for i in range(0, len(text), _TOKEN_CHUNK_SIZE):
        chunk = text[i : i + _TOKEN_CHUNK_SIZE]
        await broadcaster.publish(
            session_id, {"type": "agent_token", "data": {"text": chunk}}
        )
        await asyncio.sleep(_TOKEN_DELAY)


async def _load_conversation_history(session_id: str) -> list[dict]:
    """Load prior messages from DB to give the agent conversation context."""

    messages = []
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.id)
            )
            for msg in result.scalars().all():
                event_type = (msg.metadata_ or {}).get("event_type", "")
                if msg.role == "user":
                    messages.append({"role": "user", "content": msg.content})
                elif msg.role == "assistant" and event_type == "agent_message":
                    messages.append({"role": "assistant", "content": msg.content})
    except Exception as e:
        logger.error("Failed to load history: %s", e)
    return messages


def _load_prev_context(session_id: str, stage: str) -> str:
    """Load reports from previous stages for context injection."""
    prev_context = "(No previous context available)"

    # Try loading reports from common prior stages
    prior_stages = []
    if stage in ("prep", "data_prep"):
        prior_stages = ["eda"]
    elif stage in ("train", "trainer"):
        prior_stages = ["prep", "data_prep", "eda"]
    elif stage == "feature_eng":
        prior_stages = ["prep", "data_prep", "eda"]
    elif stage == "reviewer":
        prior_stages = ["train", "prep", "data_prep", "eda"]

    loaded = []
    for prev_stage in prior_stages:
        report_path = f"/sessions/{session_id}/{prev_stage}/report.md"
        try:
            text = read_volume_file(report_path).decode("utf-8", errors="replace")
            if text.strip():
                loaded.append(f"## {prev_stage.upper()} Report\n{text}")
                logger.info("Loaded %s report (%d chars)", prev_stage, len(text))
        except Exception:
            pass

    if loaded:
        prev_context = "\n\n---\n\n".join(loaded)

    # For train stages, also inject prep metadata.json
    if stage in ("train", "trainer"):
        metadata_path = f"/sessions/{session_id}/prep/data/metadata.json"
        try:
            metadata_text = read_volume_file(metadata_path).decode(
                "utf-8", errors="replace"
            )
            prev_context += f"\n\n## Prep Metadata\n```json\n{metadata_text}\n```"
        except Exception:
            pass

    return prev_context


async def run_agent(
    session_id: str,
    experiment_id: str,
    stage: str,
    instructions: str,
    dataset_ref: str = "",
    user_prompt: str | None = None,
    gpu: str | None = None,
    model: str | None = None,
    agent_type: str | None = None,
    depth: int = 0,
):
    """Run an agent. agent_type maps to a YAML in agents/. Falls back to stage name."""

    # Resolve agent_type: explicit param > stage name > "orchestrator" for general use
    if not agent_type:
        # Map legacy stage names to agent types
        stage_to_agent = {
            "eda": "eda",
            "prep": "data_prep",
            "train": "trainer",
            "orchestrator": "orchestrator",
            "chat": "chat",
        }
        agent_type = stage_to_agent.get(stage, stage)

    collected_text = ""

    try:
        prev_context = _load_prev_context(session_id, stage)

        system_prompt = render_agent_system_prompt(
            agent_type,
            experiment_id=experiment_id,
            session_id=session_id,
            instructions=instructions,
            prev_context=prev_context,
        )

        if user_prompt:
            prompt = user_prompt
        else:
            prompt = get_agent_opener(agent_type)

        await save_and_publish(
            session_id,
            "state_change",
            {"state": f"{stage}_running", "agent_type": agent_type},
            role="system",
        )

        # Resolve model: explicit param > agent default > global config
        model = model or get_agent_default_model(agent_type) or settings.claude_model

        # Load conversation history for follow-up messages
        if user_prompt:
            history = await _load_conversation_history(session_id)
            if history:
                context_parts = []
                for msg in history[:-1]:
                    prefix = "User" if msg["role"] == "user" else "Assistant"
                    context_parts.append(f"{prefix}: {msg['content']}")
                if context_parts:
                    conversation_context = "\n\n".join(context_parts)
                    system_prompt += (
                        f"\n\n## Prior conversation\n{conversation_context}"
                    )

        # Create MCP server with tools determined by the agent's YAML
        mcp_server = create_mcp_server(
            session_id,
            experiment_id,
            stage,
            gpu=gpu,
            agent_type=agent_type,
            depth=depth,
            instructions=instructions,
            model=model,
        )

        # Build tool list from agent config
        agent_tools = get_agent_tools(agent_type)
        tool_names = [f"mcp__trainable__{t}" for t in agent_tools]
        # Only include delegate_task if depth allows it
        from .agents import can_delegate

        if "delegate_task" in agent_tools and not can_delegate(agent_type, depth):
            tool_names = [t for t in tool_names if "delegate_task" not in t]

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            permission_mode="bypassPermissions",
            max_turns=settings.agent_max_turns,
            stderr=lambda line: (
                logger.debug("CLI: %s", line.strip()) if line.strip() else None
            ),
            tools=tool_names,
            allowed_tools=tool_names,
            mcp_servers={"trainable": mcp_server},
            env={"CLAUDE_CODE_OAUTH_TOKEN": settings.claude_code_oauth_token},
        )

        logger.info(
            "Starting agent=%s stage=%s session=%s model=%s depth=%d tools=%s",
            agent_type,
            stage,
            session_id,
            model,
            depth,
            agent_tools,
        )

        async with asyncio.timeout(settings.agent_timeout_seconds):
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text") and block.text:
                            collected_text += block.text
                            # Stream tokens via SSE
                            await _stream_text(session_id, block.text)
                            # Save complete text to DB only (no SSE)
                            await save_and_publish(
                                session_id,
                                "agent_message",
                                {"text": block.text},
                                role="assistant",
                                publish=False,
                            )
                            logger.info("Agent text: %s", block.text[:120])

                elif isinstance(message, ResultMessage):
                    logger.info("Agent %s done", agent_type)

        # After agent finishes, read back the report and file list from volume
        await publish_artifacts(session_id, experiment_id, stage)

        # Post-stage hooks: validation, S3 sync, metadata extraction
        await post_stage_hook(session_id, experiment_id, stage)

        await save_and_publish(
            session_id,
            "state_change",
            {"state": f"{stage}_done", "agent_type": agent_type},
            role="system",
        )

    except TimeoutError:
        logger.error(
            "Agent %s timed out after %ds for session %s",
            agent_type,
            settings.agent_timeout_seconds,
            session_id,
        )
        await save_and_publish(
            session_id,
            "agent_error",
            {"error": f"Agent timed out after {settings.agent_timeout_seconds}s"},
            role="system",
        )
        await save_and_publish(
            session_id, "state_change", {"state": "failed"}, role="system"
        )

    except asyncio.CancelledError:
        silent = session_id in _silent_aborts
        _silent_aborts.discard(session_id)
        logger.info(
            "Cancelled: agent=%s session=%s silent=%s",
            agent_type,
            session_id,
            silent,
        )
        if not silent:
            await save_and_publish(
                session_id,
                "agent_aborted",
                {"reason": "user_cancelled", "stage": stage},
                role="system",
            )
            await save_and_publish(
                session_id, "state_change", {"state": "cancelled"}, role="system"
            )

    except Exception as e:
        logger.exception("Error in agent %s for session %s", agent_type, session_id)
        await save_and_publish(
            session_id, "agent_error", {"error": str(e)}, role="system"
        )
        await save_and_publish(
            session_id, "state_change", {"state": "failed"}, role="system"
        )
        raise

    finally:
        from .tasks import cleanup_session

        cleanup_session(session_id)

    return collected_text
