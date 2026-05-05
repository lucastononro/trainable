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

from observability import agent_span, bind_log_context, clear_log_context
from services.broadcaster import broadcaster
from services.usage import compute_llm_cost, record_llm_usage

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
                # Sort for cache prefix stability — listdir order is not
                # guaranteed and would invalidate the prompt-cache hit.
                entries.sort()
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

    # Per-turn usage accumulator. claude-agent-sdk yields usage in two
    # different shapes depending on the surface:
    #   - AssistantMessage.usage  → snake_case (input_tokens, output_tokens,
    #     cache_*_input_tokens) — Anthropic API raw shape.
    #   - ResultMessage.model_usage[model] → camelCase (inputTokens,
    #     outputTokens, cacheReadInputTokens, cacheCreationInputTokens,
    #     costUSD).
    # We normalize to snake_case so the rest of the cost path is uniform
    # regardless of provider/SDK convention.
    _accumulated_usage: dict[str, dict] = {}

    # camelCase → snake_case key map covering Anthropic + claude-agent-sdk
    # variants. Add entries here when a new provider lands with a different
    # casing convention; the rest of the stack stays unchanged.
    _USAGE_KEY_ALIASES: dict[str, str] = {
        "inputTokens": "input_tokens",
        "outputTokens": "output_tokens",
        "cacheReadInputTokens": "cache_read_input_tokens",
        "cacheCreationInputTokens": "cache_creation_input_tokens",
        "promptTokens": "input_tokens",
        "completionTokens": "output_tokens",
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
        "costUSD": "cost_usd",
    }

    def _normalize_usage(u: dict | None) -> dict:
        """Coerce a usage dict into snake_case Anthropic-shaped keys."""
        if not isinstance(u, dict):
            return {}
        out: dict = {}
        for k, v in u.items():
            target = _USAGE_KEY_ALIASES.get(k, k)
            try:
                if target == "cost_usd":
                    out[target] = float(v or 0.0)
                else:
                    out[target] = int(v or 0)
            except (TypeError, ValueError):
                continue
        return out

    def _bump_usage(model_name: str, turn_usage: dict | None) -> None:
        norm = _normalize_usage(turn_usage)
        if not norm:
            return
        bucket = _accumulated_usage.setdefault(model_name, {})
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            v = norm.get(k, 0)
            try:
                bucket[k] = bucket.get(k, 0) + int(v)
            except (TypeError, ValueError):
                continue

    # Live LLM cost feedback. claude-agent-sdk only fires ResultMessage at the
    # END of an agent run, so without these per-turn broadcasts the LLM-cost
    # tile in the badge sits at 0 for the entire run while the user watches
    # `execute-code` rack up sandbox time. We broadcast a synthetic
    # `usage_event` per AssistantMessage with that turn's usage delta. The
    # canonical DB row still gets written at ResultMessage time via
    # record_llm_usage(broadcast=False) — no double-count.
    #
    # Dedupe: the SDK occasionally emits AssistantMessage twice for one turn
    # (streaming partial + complete). message_id is stable per turn; fall
    # back to a usage-tuple key when missing.
    _seen_partial_keys: set = set()

    async def _broadcast_partial_llm(
        message_obj,
        turn_model: str,
        norm_usage: dict,
    ) -> None:
        in_tok = int(norm_usage.get("input_tokens", 0) or 0)
        out_tok = int(norm_usage.get("output_tokens", 0) or 0)
        cache_r = int(norm_usage.get("cache_read_input_tokens", 0) or 0)
        cache_w = int(norm_usage.get("cache_creation_input_tokens", 0) or 0)
        if in_tok == 0 and out_tok == 0 and cache_r == 0 and cache_w == 0:
            return  # nothing to report

        mid = getattr(message_obj, "message_id", None) or getattr(
            message_obj, "uuid", None
        )
        dedupe_key = mid or (turn_model, in_tok, out_tok, cache_r, cache_w)
        if dedupe_key in _seen_partial_keys:
            return
        _seen_partial_keys.add(dedupe_key)

        cost = compute_llm_cost(
            model=turn_model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_input_tokens=cache_r,
            cache_creation_input_tokens=cache_w,
        )

        try:
            await broadcaster.publish(
                session_id,
                {
                    "type": "usage_event",
                    "data": {
                        "kind": "llm",
                        "agent_type": agent_type,
                        "agent_id": agent_id,
                        "provider": "claude",
                        "model": turn_model,
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "cache_read_input_tokens": cache_r,
                        "cache_creation_input_tokens": cache_w,
                        "sandbox_seconds": 0.0,
                        "cost_usd": cost,
                        "is_partial": True,
                    },
                },
            )
        except Exception as e:
            logger.debug("Partial usage broadcast failed: %s", e)

    bind_log_context(
        session_id=session_id,
        agent_type=agent_type,
        agent_id=agent_id,
        depth=depth,
    )

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

        # Open the OTel span over the whole SDK loop. Using a manual
        # __enter__/__exit__ keeps the existing async-for indentation
        # untouched — important because nested-with would re-indent ~100
        # lines of message-handling code.
        _agent_span_cm = agent_span(
            agent_type=agent_type,
            session_id=session_id,
            model=model,
            depth=depth,
            agent_id=agent_id,
        )
        _agent_span = _agent_span_cm.__enter__()
        try:
            _agent_span.set_attribute("agent.parent_id", parent_agent_id or "")
            _agent_span.set_attribute("agent.tools.count", len(agent_tools))
        except Exception:
            pass

        # Collected user-side text for the lower-bound output token estimate
        # we use when the SDK doesn't surface usage at all (some OAuth paths).
        _collected_assistant_text = ""
        # Track whether the SDK ever gave us real usage. If not, we'll
        # fall back to a char-based heuristic at the end of the run.
        _saw_real_usage = False

        async with asyncio.timeout(settings.agent_timeout_seconds):
            async for message in query(prompt=prompt, options=options):
                # One-line dump of every message type the SDK yields. INFO
                # level for now so OAuth-quirk debugging is visible without
                # log_level=DEBUG. Trim heavy fields so logs stay readable.
                try:
                    _msg_type = type(message).__name__
                    _msg_usage = getattr(message, "usage", None)
                    _msg_model = getattr(message, "model", None)
                    _msg_subtype = getattr(message, "subtype", None)
                    logger.info(
                        "[sdk-event] %s subtype=%s model=%s usage=%s",
                        _msg_type,
                        _msg_subtype,
                        _msg_model,
                        _msg_usage,
                    )
                except Exception:
                    pass

                if isinstance(message, AssistantMessage):
                    # Per-turn usage. AssistantMessage.usage is the source of
                    # truth for OAuth users (where ResultMessage.usage is
                    # often empty). Accumulate against the message's model.
                    turn_usage = getattr(message, "usage", None)
                    turn_model = getattr(message, "model", None) or model
                    if turn_usage:
                        _saw_real_usage = True
                    _bump_usage(turn_model, turn_usage)
                    # Live LLM cost feedback — broadcast a partial usage_event
                    # so the badge ticks up during the run, not just at end.
                    if turn_usage:
                        await _broadcast_partial_llm(
                            message, turn_model, _normalize_usage(turn_usage)
                        )
                    for block in message.content:
                        # 1) Plain text — keep the existing agent_message stream
                        #    that the frontend already renders, AND mirror it
                        #    into the agent_thought stream so inspectors see it.
                        if hasattr(block, "text") and getattr(block, "text", None):
                            text = block.text
                            collected_text += text
                            _collected_assistant_text += text
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
                    # Record token usage. Resolution order, in priority:
                    #   1. ResultMessage.model_usage  (per-model aggregate)
                    #   2. ResultMessage.usage        (single-model aggregate)
                    #   3. _accumulated_usage         (per-turn fallback —
                    #      the Claude OAuth path leaves 1/2 empty)
                    # Each call records via record_llm_usage which spawns
                    # its own span via observability.record_usage_attributes.
                    logger.info(
                        "ResultMessage usage shapes — usage=%s, model_usage=%s, "
                        "total_cost_usd=%s, accumulated_models=%s",
                        message.usage,
                        message.model_usage,
                        message.total_cost_usd,
                        list(_accumulated_usage.keys()),
                    )

                    sdk_has_usage = bool(message.model_usage) or bool(message.usage)
                    if not sdk_has_usage and _accumulated_usage:
                        logger.info(
                            "Using per-turn accumulated usage as fallback "
                            "(SDK aggregate was empty — likely OAuth path)"
                        )

                    # Pick the canonical source for span attributes.
                    if message.usage:
                        agg = message.usage
                    elif _accumulated_usage:
                        # Sum across models for the agent-span aggregate.
                        agg = {}
                        for bucket in _accumulated_usage.values():
                            for k, v in bucket.items():
                                agg[k] = agg.get(k, 0) + v
                    else:
                        agg = {}

                    try:
                        try:
                            _agent_span.set_attribute(
                                "llm.input_tokens",
                                int(agg.get("input_tokens", 0) or 0),
                            )
                            _agent_span.set_attribute(
                                "llm.output_tokens",
                                int(agg.get("output_tokens", 0) or 0),
                            )
                            _agent_span.set_attribute(
                                "llm.cache_read_input_tokens",
                                int(agg.get("cache_read_input_tokens", 0) or 0),
                            )
                            if message.total_cost_usd is not None:
                                _agent_span.set_attribute(
                                    "llm.cost_usd", float(message.total_cost_usd)
                                )
                        except Exception:
                            pass

                        # Decision tree, in order:
                        #   1. ResultMessage.model_usage  → per-model rows
                        #      (preferred — gives correct per-model cost
                        #      attribution since each model has its own
                        #      pricing). camelCase keys are normalized.
                        #   2. ResultMessage.usage        → single row
                        #      (the aggregate; mis-attributes when multiple
                        #      models ran). Used only when (1) is empty.
                        #   3. _accumulated_usage         → per-turn fallback
                        #      built from AssistantMessage.usage events.
                        #   4. Char-based heuristic       → if nothing above
                        #      gave us real tokens.
                        def _has_tokens(u) -> bool:
                            if not isinstance(u, dict):
                                return False
                            n = _normalize_usage(u)
                            return (
                                int(n.get("input_tokens", 0) or 0) > 0
                                or int(n.get("output_tokens", 0) or 0) > 0
                            )

                        # Normalize all three potential sources up-front.
                        norm_model_usage = {
                            m_name: _normalize_usage(m_usage)
                            for m_name, m_usage in (message.model_usage or {}).items()
                        }
                        norm_message_usage = _normalize_usage(message.usage)

                        model_usage_has_tokens = any(
                            _has_tokens(u) for u in norm_model_usage.values()
                        )
                        message_usage_has_tokens = _has_tokens(norm_message_usage)
                        accumulated_has_tokens = any(
                            _has_tokens(u) for u in _accumulated_usage.values()
                        )

                        if not (
                            model_usage_has_tokens
                            or message_usage_has_tokens
                            or accumulated_has_tokens
                        ):
                            logger.warning(
                                "[cost] SDK returned no non-zero token counts. "
                                "raw usage=%r model_usage=%r accumulated=%r",
                                message.usage,
                                message.model_usage,
                                dict(_accumulated_usage),
                            )

                        # broadcast=False on every record_llm_usage call: live
                        # SSE has already been emitted per-turn via
                        # `_broadcast_partial_llm` above. The DB row is the
                        # canonical source for hydration on session reload —
                        # broadcasting it again would double-count in the
                        # frontend's accumulator.
                        if model_usage_has_tokens:
                            for m_name, m_usage in norm_model_usage.items():
                                if not _has_tokens(m_usage):
                                    continue
                                await record_llm_usage(
                                    session_id=session_id,
                                    agent_type=agent_type,
                                    agent_id=agent_id,
                                    provider="claude",
                                    model=m_name,
                                    usage=m_usage,
                                    is_error=bool(message.is_error),
                                    broadcast=False,
                                )
                        elif message_usage_has_tokens:
                            await record_llm_usage(
                                session_id=session_id,
                                agent_type=agent_type,
                                agent_id=agent_id,
                                provider="claude",
                                model=model,
                                usage=norm_message_usage,
                                is_error=bool(message.is_error),
                                broadcast=False,
                            )
                        elif accumulated_has_tokens:
                            # OAuth fallback: write one row per model we saw
                            # across AssistantMessage turns.
                            for m_name, m_usage in _accumulated_usage.items():
                                if not _has_tokens(m_usage):
                                    continue
                                await record_llm_usage(
                                    session_id=session_id,
                                    agent_type=agent_type,
                                    agent_id=agent_id,
                                    provider="claude",
                                    model=m_name,
                                    usage=m_usage,
                                    is_error=bool(message.is_error),
                                    broadcast=False,
                                )
                        else:
                            # Last-resort heuristic: SDK gave us NO usage on
                            # any event (Claude Code OAuth + older claude-
                            # agent-sdk versions sometimes leaves it null).
                            # Estimate from char length so the user sees a
                            # ballpark cost instead of zero. Tag the row so
                            # later we can distinguish estimated vs real.
                            est_in = max(0, (len(prompt) + len(system_prompt)) // 4)
                            est_out = max(0, len(_collected_assistant_text) // 4)
                            if est_in or est_out:
                                logger.warning(
                                    "[cost] SDK never reported usage — falling "
                                    "back to char-based estimate (in≈%d out≈%d). "
                                    "Tool calls + intermediate context not counted, "
                                    "so this is a LOWER bound.",
                                    est_in,
                                    est_out,
                                )
                                # Broadcast THIS one — heuristic path means no
                                # partials were fired, so the canonical event
                                # IS the user's only signal.
                                await record_llm_usage(
                                    session_id=session_id,
                                    agent_type=agent_type,
                                    agent_id=agent_id,
                                    provider="claude",
                                    model=model,
                                    usage={
                                        "input_tokens": est_in,
                                        "output_tokens": est_out,
                                    },
                                    is_error=bool(message.is_error),
                                    extra={"estimate": "char_div_4"},
                                )
                    except Exception as e:
                        logger.warning("record_llm_usage failed: %s", e)

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
        # Close the agent span (manually opened above so we didn't have to
        # re-indent the SDK message loop). Pass exc info if we exited via
        # an unhandled exception path.
        try:
            cm = locals().get("_agent_span_cm")
            if cm is not None:
                import sys

                cm.__exit__(*sys.exc_info())
        except Exception:
            pass
        clear_log_context()
        # Only clean up session-wide state when the root run finishes — sub-agents
        # share the same session and would otherwise wipe each other out.
        if depth == 0:
            from .tasks import cleanup_session

            cleanup_session(session_id)

    return collected_text
