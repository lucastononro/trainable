"""Core agent loop — orchestrates Claude Agent SDK calls."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    UserMessage,
    query,
)
from sqlalchemy import select

from config import settings
from db import async_session
from models import Artifact, Experiment, Message, ProcessedDatasetMeta, Project
from services.volume import (
    listdir_async,
    read_volume_file_async,
    reload_volume_async,
)

from .agents import (
    get_agent_default_model,
    get_agent_opener,
    get_agent_tools,
    render_agent_system_prompt,
)
from .events import post_stage_hook, publish_artifacts, save_and_publish
from .tasks import _silent_aborts
from .tools import create_mcp_server

logger = logging.getLogger(__name__)

# Cap each persisted thought block (text, tool_use, tool_result) at this many
# characters. Tool results from execute_code can be megabytes — never store the
# raw blob, just enough for an inspector to understand what happened.
_THOUGHT_BLOCK_MAX_CHARS = 1500


_MENTION_SENTINEL_START = "\ue000"
_MENTION_SENTINEL_END = "\ue001"


def _apply_mentions(user_prompt: str, mentions: list[dict] | None) -> str:
    """Strip mention sentinels (\\uE000<index>\\uE001) and append a references block.

    Frontend stores mentions inline via private-use-area sentinels so the human
    text stays linear. The agent only needs the structured reference list, so
    we flatten the sentinels to the label and append a trailing block with
    canonical paths / session ids.
    """
    if not user_prompt:
        return user_prompt

    cleaned = user_prompt
    if mentions:

        def _replace(match):
            try:
                idx = int(match.group(1))
            except (ValueError, TypeError):
                return ""
            if 0 <= idx < len(mentions):
                return f"@{mentions[idx].get('label', '')}"
            return ""

        import re

        cleaned = re.sub(
            f"{_MENTION_SENTINEL_START}(\\d+){_MENTION_SENTINEL_END}", _replace, cleaned
        )
    else:
        cleaned = cleaned.replace(_MENTION_SENTINEL_START, "").replace(
            _MENTION_SENTINEL_END, ""
        )

    if not mentions:
        return cleaned

    lines = ["", "[Mentioned references:"]
    for m in mentions:
        kind = m.get("kind")
        label = m.get("label", "")
        ref = m.get("ref", "")
        if kind == "file":
            path = m.get("sandbox_path") or ref
            lines.append(f'- file "{label}" at {path}')
        elif kind == "session":
            lines.append(
                f'- session "{label}" (id: {ref}) — inspect with read_project_session'
            )
        else:
            lines.append(f'- {kind} "{label}" ref={ref}')
    lines.append("]")
    return (
        cleaned + "\n".join(lines)
        if cleaned.endswith("\n")
        else cleaned + "\n" + "\n".join(lines)
    )


def _truncate(
    text: str, limit: int = _THOUGHT_BLOCK_MAX_CHARS
) -> tuple[str, bool, int]:
    """Return (truncated_text, was_truncated, original_bytes)."""
    if text is None:
        return "", False, 0
    original = len(text)
    if original <= limit:
        return text, False, original
    return text[:limit] + f"\n\n…[truncated {original - limit} chars]", True, original


def _block_to_text(block) -> str:
    """Best-effort serialization of a tool_use input or tool_result content."""
    if isinstance(block, str):
        return block
    if isinstance(block, (dict, list)):
        try:
            return json.dumps(block, default=str, ensure_ascii=False)
        except Exception:
            return str(block)
    return str(block)


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
                meta = msg.metadata_ or {}
                event_type = meta.get("event_type", "")
                # Legacy seeded intros lived in assistant agent_message rows.
                # They duplicate the project-files injection in the system
                # prompt, so skip them.
                if meta.get("session_intro"):
                    continue
                if msg.role == "user":
                    messages.append({"role": "user", "content": msg.content})
                elif msg.role == "assistant" and event_type == "agent_message":
                    messages.append({"role": "assistant", "content": msg.content})
    except Exception as e:
        logger.error("Failed to load history: %s", e)
    return messages


async def _load_project_context(experiment_id: str) -> tuple[str, str, str]:
    """Return (project_id, project_name, project_files_listing) for an experiment.

    project_files_listing is a multi-line string describing all files currently
    present under /projects/{project_id}/datasets/. If the project has no data,
    returns the placeholder "(no data uploaded yet)".
    """
    project_id = ""
    project_name = ""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Experiment).where(Experiment.id == experiment_id)
            )
            experiment = result.scalar_one_or_none()
            if experiment and experiment.project_id:
                project_id = experiment.project_id
                proj_result = await db.execute(
                    select(Project).where(Project.id == project_id)
                )
                project = proj_result.scalar_one_or_none()
                if project:
                    project_name = project.name
    except Exception as e:
        logger.warning("Failed to load project for experiment %s: %s", experiment_id, e)

    files_listing = "(no data uploaded yet)"
    if project_id:
        await reload_volume_async()
        try:
            entries = []
            datasets_root = f"/projects/{project_id}/datasets"
            for entry in await listdir_async(datasets_root, recursive=True):
                if entry.type.name != "FILE":
                    continue
                display = entry.path
                if display.startswith("/"):
                    display = display[1:]
                entries.append(f"- /data/{display}")
            if entries:
                files_listing = "\n".join(entries[:50])
                if len(entries) > 50:
                    files_listing += f"\n  …({len(entries) - 50} more)"
                logger.info(
                    "Project context: %d data files for project %s",
                    len(entries),
                    project_id,
                )
            else:
                logger.info(
                    "Project context: project %s datasets dir is empty", project_id
                )
        except FileNotFoundError:
            logger.info(
                "Project context: no datasets folder yet for project %s", project_id
            )
        except Exception as e:
            logger.warning(
                "Project context: failed to list datasets for %s: %s",
                project_id,
                e,
            )
            files_listing = (
                "(could not list project files — listing unavailable, but the "
                "project may still have data; check `/data/projects/"
                + project_id
                + "/datasets/` directly)"
            )

    return project_id, project_name, files_listing


async def _load_prev_context(session_id: str, stage: str) -> str:
    """Load reports from previous agents for context injection.

    Sources of truth are now the DB (Artifact table for reports,
    ProcessedDatasetMeta for prep schema) — agents no longer need to live
    in fixed stage subfolders.
    """
    prev_context = "(No previous context available)"

    # Which prior producers does the current agent care about?
    relevance = {
        "prep": {"eda"},
        "data_prep": {"eda"},
        "train": {"data_prep", "prep", "eda", "feature_eng"},
        "trainer": {"data_prep", "prep", "eda", "feature_eng"},
        "feature_eng": {"data_prep", "prep", "eda"},
        "reviewer": {"trainer", "train", "data_prep", "prep", "eda", "feature_eng"},
    }
    wanted = relevance.get(stage)

    loaded: list[str] = []
    prep_metadata_text: str | None = None
    try:
        async with async_session() as db:
            q = (
                select(Artifact)
                .where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "report",
                )
                .order_by(Artifact.created_at.desc())
            )
            reports = list((await db.execute(q)).scalars().all())

            seen_producers: set[str] = set()
            for art in reports:
                producer = (art.stage or "").lower()
                if wanted is not None and producer not in wanted:
                    continue
                if producer in seen_producers:
                    continue
                seen_producers.add(producer)
                try:
                    raw = await read_volume_file_async(art.path)
                    text = raw.decode("utf-8", errors="replace")
                except Exception:
                    continue
                if text.strip():
                    header = (producer.upper() if producer else "PRIOR") + " Report"
                    loaded.append(f"## {header}\n{text}")
                    logger.info(
                        "Loaded %s report from %s (%d chars)",
                        producer,
                        art.path,
                        len(text),
                    )

            if stage in ("train", "trainer"):
                meta_row = await db.execute(
                    select(ProcessedDatasetMeta).where(
                        ProcessedDatasetMeta.session_id == session_id
                    )
                )
                meta = meta_row.scalar_one_or_none()
                if meta:
                    prep_metadata_text = json.dumps(
                        meta.to_dict(), default=str, indent=2
                    )
    except Exception as e:
        logger.warning("Failed to load prior context from DB: %s", e)

    if loaded:
        prev_context = "\n\n---\n\n".join(loaded)

    if prep_metadata_text:
        prev_context += f"\n\n## Prep Metadata\n```json\n{prep_metadata_text}\n```"

    return prev_context


async def run_agent(
    session_id: str,
    experiment_id: str,
    stage: str,
    instructions: str,
    dataset_ref: str = "",
    user_prompt: str | None = None,
    sandbox_config: dict | None = None,
    model: str | None = None,
    agent_type: str | None = None,
    depth: int = 0,
    agent_models: dict | None = None,
    agent_id: str = "root",
    parent_agent_id: str | None = None,
    mentions: list[dict] | None = None,
):
    """Run an agent. agent_type maps to a YAML in agents/. Falls back to stage name.

    agent_models is a per-agent model override map: {"eda": "claude-haiku-4-5", ...}

    agent_id uniquely identifies this run inside the session. The top-level
    caller passes "root"; nested calls from delegate_task pass a fresh uuid
    fragment so messages produced by each agent can be filtered later.
    """

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

    agent_meta = {
        "agent_id": agent_id,
        "agent_type": agent_type,
        "parent_agent_id": parent_agent_id,
        "depth": depth,
    }

    async def _publish(
        event_type: str, data: dict, role: str | None = None, publish: bool = True
    ):
        await save_and_publish(
            session_id,
            event_type,
            data,
            role=role,
            publish=publish,
            agent_meta=agent_meta,
        )

    collected_text = ""

    try:
        prev_context = await _load_prev_context(session_id, stage)
        project_id, project_name, project_files = await _load_project_context(
            experiment_id
        )

        system_prompt = render_agent_system_prompt(
            agent_type,
            experiment_id=experiment_id,
            session_id=session_id,
            instructions=instructions,
            prev_context=prev_context,
            project_id=project_id,
            project_name=project_name,
            project_files=project_files,
        )

        # Inject the wall-clock time so the agent can compare its "now" against
        # message timestamps when inspecting another agent's context.
        now_iso = datetime.now(timezone.utc).isoformat()
        system_prompt += (
            f"\n\n## Runtime context\n"
            f"- Current time (UTC): {now_iso}\n"
            f"- Your agent_id: {agent_id}\n"
            f"- Your agent_type: {agent_type}\n"
            f"- Your depth: {depth}\n"
            + (f"- Parent agent_id: {parent_agent_id}\n" if parent_agent_id else "")
            + "- Every message in this session is timestamped (created_at). When you call "
            "inspect_agent_context, each block carries its created_at so you can tell what "
            "is recent vs. stale relative to the time above."
        )

        if user_prompt:
            prompt = _apply_mentions(user_prompt, mentions)
        else:
            prompt = get_agent_opener(agent_type)

        await _publish(
            "state_change",
            {"state": f"{stage}_running", "agent_type": agent_type},
            role="system",
        )

        # Resolve model: explicit param > agent default > global config
        # Resolution order: explicit param > per-agent override > agent YAML default > global config
        override_model = (agent_models or {}).get(agent_type)
        model = (
            model
            or override_model
            or get_agent_default_model(agent_type)
            or settings.claude_model
        )

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
            sandbox_config=sandbox_config or {},
            agent_type=agent_type,
            depth=depth,
            instructions=instructions,
            model=model,
            agent_models=agent_models or {},
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
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
            "Starting agent=%s id=%s parent=%s stage=%s session=%s model=%s depth=%d tools=%s",
            agent_type,
            agent_id,
            parent_agent_id,
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
                        # 1) Plain text — keep the existing agent_message stream
                        #    that the frontend already renders, AND mirror it
                        #    into the agent_thought stream so inspectors see it.
                        if hasattr(block, "text") and getattr(block, "text", None):
                            text = block.text
                            collected_text += text
                            await _publish(
                                "agent_message",
                                {"text": text},
                                role="assistant",
                            )
                            truncated_text, was_trunc, orig_bytes = _truncate(text)
                            await _publish(
                                "agent_thought",
                                {
                                    "text": truncated_text,
                                    "block_type": "text",
                                    "truncated": was_trunc,
                                    "original_bytes": orig_bytes,
                                },
                                role="assistant",
                                publish=False,
                            )
                            logger.info("Agent text: %s", text[:120])
                            continue

                        # 2) Tool use — record the tool name and (truncated) input.
                        tool_name = getattr(block, "name", None)
                        tool_input = getattr(block, "input", None)
                        tool_use_id = getattr(block, "id", None)
                        if tool_name is not None and tool_input is not None:
                            payload_text = _block_to_text(tool_input)
                            truncated_text, was_trunc, orig_bytes = _truncate(
                                payload_text
                            )
                            await _publish(
                                "agent_thought",
                                {
                                    "text": truncated_text,
                                    "block_type": "tool_use",
                                    "tool_name": tool_name,
                                    "tool_use_id": tool_use_id,
                                    "truncated": was_trunc,
                                    "original_bytes": orig_bytes,
                                },
                                role="assistant",
                                publish=False,
                            )
                            logger.info("Agent tool_use: %s", tool_name)

                elif isinstance(message, UserMessage):
                    # Tool results come back framed as a UserMessage with
                    # ToolResultBlock content. Persist a truncated copy.
                    for block in getattr(message, "content", []) or []:
                        tool_use_id = getattr(block, "tool_use_id", None)
                        if tool_use_id is None:
                            continue
                        raw = getattr(block, "content", None)
                        if isinstance(raw, list):
                            parts = []
                            for item in raw:
                                # mcp TextContent objects expose .text; dicts use ['text']
                                if hasattr(item, "text"):
                                    parts.append(item.text or "")
                                elif isinstance(item, dict):
                                    parts.append(item.get("text", ""))
                                else:
                                    parts.append(str(item))
                            payload_text = "\n".join(p for p in parts if p)
                        else:
                            payload_text = _block_to_text(raw)
                        truncated_text, was_trunc, orig_bytes = _truncate(payload_text)
                        is_error = bool(getattr(block, "is_error", False))
                        await _publish(
                            "agent_thought",
                            {
                                "text": truncated_text,
                                "block_type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "is_error": is_error,
                                "truncated": was_trunc,
                                "original_bytes": orig_bytes,
                            },
                            role="user",
                            publish=False,
                        )

                elif isinstance(message, ResultMessage):
                    logger.info("Agent %s done", agent_type)

        # After agent finishes, read back the report and file list from volume
        await publish_artifacts(session_id, experiment_id, stage)

        # Post-stage hooks: validation, S3 sync, metadata extraction
        await post_stage_hook(session_id, experiment_id, stage)

        await _publish(
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
        await _publish(
            "agent_error",
            {"error": f"Agent timed out after {settings.agent_timeout_seconds}s"},
            role="system",
        )
        await _publish("state_change", {"state": "failed"}, role="system")

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
            await _publish(
                "agent_aborted",
                {"reason": "user_cancelled", "stage": stage},
                role="system",
            )
            await _publish("state_change", {"state": "cancelled"}, role="system")

    except Exception as e:
        logger.exception("Error in agent %s for session %s", agent_type, session_id)
        await _publish("agent_error", {"error": str(e)}, role="system")
        await _publish("state_change", {"state": "failed"}, role="system")
        raise

    finally:
        # Only clean up session-wide state when the root run finishes — sub-agents
        # share the same session and would otherwise wipe each other out.
        if depth == 0:
            from .tasks import cleanup_session

            cleanup_session(session_id)

    return collected_text
