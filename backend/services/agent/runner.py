"""Core agent loop — orchestrates LLM provider calls via the factory.

For Claude (`supports_mcp=True`), the provider exposes the Claude Agent SDK's
MCP-aware loop and dispatches tool calls internally. For other providers, the
runner owns the tool-execution loop: it dispatches each tool_call event to a
skill handler and feeds the result back as the next round's input messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from config import settings
from db import async_session
from models import Artifact, Experiment, Message, ProcessedDatasetMeta, Project
from services.llm import factory as llm_factory
from services.skills import build_skill_entries
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
    get_agent_provider,
    get_agent_skills,
    get_skill_for_agent,
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


def _normalize_handler_text(result) -> tuple[str, bool]:
    """Coerce a skill-handler return into (text, is_error).

    Handlers return {"content": [{"type":"text","text":"..."}], "is_error": bool?}.
    """
    if isinstance(result, dict):
        is_error = bool(result.get("is_error"))
        parts = []
        for item in result.get("content", []) or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif hasattr(item, "text"):
                parts.append(item.text or "")
            else:
                parts.append(str(item))
        text = "\n".join(p for p in parts if p) or "(no output)"
        return text, is_error
    return _block_to_text(result), False


def _record_skill_specs(agent_type: str, agent_skills: list[str]) -> list[dict]:
    """Build the normalized [{name, description, input_schema}] list the
    non-Claude providers consume."""
    specs = []
    for slug in agent_skills:
        merged = get_skill_for_agent(agent_type, slug)
        specs.append(
            {
                "name": merged["name"],
                "description": merged["description"],
                "input_schema": merged["input_schema"]
                or {"type": "object", "properties": {}},
            }
        )
    return specs


async def _drive_provider(
    *,
    provider_id: str,
    prompt: str,
    system_prompt: str,
    model: str,
    agent_type: str,
    session_id: str,
    experiment_id: str,
    stage: str,
    depth: int,
    agent_id: str,
    parent_agent_id: str | None,
    agent_skills: list[str],
    sandbox_config: dict,
    instructions: str,
    agent_models: dict,
    publish,
    agent_span,
) -> str:
    """Drive one agent run via the LLM factory.

    For Claude (`supports_mcp=True`), the provider runs the MCP-aware loop
    internally and emits text/tool_call/tool_result/usage events. For other
    providers, this function maintains the messages list and dispatches each
    `tool_call` to the matching skill handler.
    """
    provider = llm_factory.get_provider(provider_id)
    caps = provider.capabilities
    collected_text = ""

    # Build skill specs + handlers once. The MCP server is only built for
    # Claude; OpenAI/Gemini/LiteLLM dispatch handlers in this function.
    skill_specs = _record_skill_specs(agent_type, agent_skills)
    skill_entries = build_skill_entries(
        agent_type=agent_type,
        session_id=session_id,
        experiment_id=experiment_id,
        stage=stage,
        depth=depth,
        publish_fn=save_and_publish,
        sandbox_config=sandbox_config,
        model=model,
        instructions=instructions,
        agent_models=agent_models,
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
    )
    skill_handlers = {slug: entry["handler"] for slug, entry in skill_entries.items()}

    async def _persist_text(text: str) -> None:
        nonlocal collected_text
        collected_text += text
        await publish("agent_message", {"text": text}, role="assistant")
        truncated_text, was_trunc, orig_bytes = _truncate(text)
        await publish(
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

    async def _persist_tool_call(tool_name: str, tool_use_id: str, args: dict) -> None:
        payload_text = _block_to_text(args)
        truncated_text, was_trunc, orig_bytes = _truncate(payload_text)
        await publish(
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

    async def _persist_tool_result(tool_use_id: str, content, is_error: bool) -> None:
        if isinstance(content, list):
            parts = []
            for item in content:
                if hasattr(item, "text"):
                    parts.append(item.text or "")
                elif isinstance(item, dict):
                    parts.append(item.get("text", ""))
                else:
                    parts.append(str(item))
            payload_text = "\n".join(p for p in parts if p)
        else:
            payload_text = _block_to_text(content)
        truncated_text, was_trunc, orig_bytes = _truncate(payload_text)
        await publish(
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

    async def _record_usage(
        model_name: str, usage: dict, is_error: bool, total_cost: float | None
    ):
        try:
            try:
                agent_span.set_attribute(
                    "llm.input_tokens", int(usage.get("input_tokens", 0) or 0)
                )
                agent_span.set_attribute(
                    "llm.output_tokens", int(usage.get("output_tokens", 0) or 0)
                )
                agent_span.set_attribute(
                    "llm.cache_read_input_tokens",
                    int(usage.get("cache_read_input_tokens", 0) or 0),
                )
                if total_cost is not None:
                    agent_span.set_attribute("llm.cost_usd", float(total_cost))
            except Exception:
                pass
            await record_llm_usage(
                session_id=session_id,
                agent_type=agent_type,
                agent_id=agent_id,
                provider=provider_id,
                model=model_name,
                usage=usage,
                is_error=is_error,
            )
        except Exception as e:
            logger.warning("record_llm_usage failed: %s", e)

    timeout_s = settings.agent_timeout_seconds

    # ---- Claude / MCP path -------------------------------------------------
    if caps.supports_mcp:
        # Build the MCP server with all skill handlers attached.
        mcp_server = create_mcp_server(
            session_id,
            experiment_id,
            stage,
            sandbox_config=sandbox_config,
            agent_type=agent_type,
            depth=depth,
            instructions=instructions,
            model=model,
            agent_models=agent_models,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        prefixed_tool_names = [f"mcp__trainable__{s}" for s in agent_skills]

        async with asyncio.timeout(timeout_s):
            async for event in provider.run(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                tools=prefixed_tool_names,
                mcp_servers={"trainable": mcp_server},
                max_turns=settings.agent_max_turns,
                timeout_seconds=timeout_s,
                env={"CLAUDE_CODE_OAUTH_TOKEN": settings.claude_code_oauth_token},
            ):
                if event.kind == "text":
                    await _persist_text(event.data.get("text", ""))
                elif event.kind == "tool_call":
                    await _persist_tool_call(
                        event.data.get("tool_name", ""),
                        event.data.get("tool_call_id", ""),
                        event.data.get("arguments", {}) or {},
                    )
                elif event.kind == "tool_result":
                    await _persist_tool_result(
                        event.data.get("tool_call_id", ""),
                        event.data.get("content"),
                        bool(event.data.get("is_error")),
                    )
                elif event.kind == "usage":
                    await _record_usage(
                        event.data.get("model") or model,
                        event.data.get("usage") or {},
                        is_error=False,
                        total_cost=event.data.get("total_cost_usd"),
                    )
                elif event.kind == "error":
                    logger.warning("Provider error: %s", event.data.get("message"))
        return collected_text

    # ---- Non-Claude / runner-managed tool loop -----------------------------
    # OpenAI/LiteLLM message shape; Gemini provider also accepts these but
    # without function-result re-injection (single-pass for Gemini in v1).
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    async with asyncio.timeout(timeout_s):
        for turn in range(settings.agent_max_turns):
            pending_calls: list[dict] = []
            assistant_text: list[str] = []

            async for event in provider.run(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                tools=skill_specs,
                max_turns=1,
                timeout_seconds=timeout_s,
                messages=messages,
            ):
                if event.kind == "text":
                    text = event.data.get("text", "")
                    if text:
                        assistant_text.append(text)
                        await _persist_text(text)
                elif event.kind == "tool_call":
                    name = event.data.get("tool_name", "")
                    call_id = (
                        event.data.get("tool_call_id") or f"call_{uuid.uuid4().hex[:8]}"
                    )
                    args = event.data.get("arguments", {}) or {}
                    pending_calls.append({"id": call_id, "name": name, "args": args})
                    await _persist_tool_call(name, call_id, args)
                elif event.kind == "usage":
                    await _record_usage(
                        event.data.get("model") or model,
                        event.data.get("usage") or {},
                        is_error=False,
                        total_cost=event.data.get("total_cost_usd"),
                    )
                elif event.kind == "error":
                    logger.warning("Provider error: %s", event.data.get("message"))

            # Append assistant turn to message history.
            assistant_msg: dict = {
                "role": "assistant",
                "content": "\n".join(assistant_text) or None,
            }
            if pending_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c["args"]),
                        },
                    }
                    for c in pending_calls
                ]
            messages.append(assistant_msg)

            if not pending_calls:
                break  # provider produced text only; conversation done.

            # Dispatch each tool call against the skill handler.
            for call in pending_calls:
                handler = skill_handlers.get(call["name"])
                if handler is None:
                    text = f"Unknown skill: {call['name']}"
                    is_error = True
                else:
                    try:
                        result = await handler(call["args"])
                        text, is_error = _normalize_handler_text(result)
                    except Exception as e:
                        logger.exception("Skill handler %s failed", call["name"])
                        text = f"Skill {call['name']} raised: {e}"
                        is_error = True
                if is_error:
                    text = f"[ERROR] {text}"
                await _persist_tool_result(call["id"], text, is_error)
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": text}
                )

    return collected_text


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
    agent_thinking: dict | None = None,
    agent_id: str = "root",
    parent_agent_id: str | None = None,
    mentions: list[dict] | None = None,
):
    """Run an agent. agent_type maps to a YAML in agents/. Falls back to stage name.

    agent_models is a per-agent model override map: {"eda": "claude-haiku-4-5", ...}
    agent_thinking is the parallel reasoning-level map: {"eda": "high", ...}.
    Levels are abstract — services/llm/thinking.py translates them per provider.

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

        # Resolve reasoning level. The model's catalog entry decides whether
        # the model supports thinking at all and what its default level is;
        # the per-agent override (UI picker) trumps when present and valid.
        from services.llm.thinking import normalize_level
        from services.usage import get_llm_catalog

        _llm_entry = get_llm_catalog().get(model) or {}
        _thinking_spec = (
            _llm_entry.get("thinking") if isinstance(_llm_entry, dict) else None
        )
        thinking_level: str | None = None
        if isinstance(_thinking_spec, dict):
            allowed_levels = _thinking_spec.get("levels") or []
            ui_level = (agent_thinking or {}).get(agent_type)
            default_level = _thinking_spec.get("default")
            chosen = (
                ui_level
                if (ui_level in allowed_levels)
                else (default_level if default_level in allowed_levels else None)
            )
            thinking_level = normalize_level(chosen) if chosen else None

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

        # Resolve provider id. The chosen model wins: if the catalog says
        # `gpt-5.4-nano-...` is provider=openai, route through OpenAI even
        # if the agent YAML still says provider=claude. Falls back to the
        # YAML when the model isn't catalog-listed (custom/override).
        from services.usage import get_llm_catalog

        _model_entry = get_llm_catalog().get(model) or {}
        _model_provider = (
            _model_entry.get("provider") if isinstance(_model_entry, dict) else None
        )
        provider_id = _model_provider or get_agent_provider(agent_type)
        agent_skills = get_agent_skills(agent_type)
        from .agents import can_delegate

        if "delegate-task" in agent_skills and not can_delegate(agent_type, depth):
            agent_skills = [s for s in agent_skills if s != "delegate-task"]

        logger.info(
            "Starting agent=%s id=%s parent=%s stage=%s session=%s provider=%s model=%s depth=%d skills=%s",
            agent_type,
            agent_id,
            parent_agent_id,
            stage,
            session_id,
            provider_id,
            model,
            depth,
            agent_skills,
        )

        # Open the OTel span manually so we can attach attributes around the
        # provider call without re-indenting the loop body.
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
            _agent_span.set_attribute("agent.skills.count", len(agent_skills))
            _agent_span.set_attribute("llm.provider", provider_id)
        except Exception:
            pass

        drive_text = await _drive_provider(
            provider_id=provider_id,
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            agent_type=agent_type,
            session_id=session_id,
            experiment_id=experiment_id,
            stage=stage,
            depth=depth,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            agent_skills=agent_skills,
            sandbox_config=sandbox_config or {},
            instructions=instructions,
            agent_models=agent_models or {},
            publish=_publish,
            agent_span=_agent_span,
        )
        collected_text += drive_text

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
