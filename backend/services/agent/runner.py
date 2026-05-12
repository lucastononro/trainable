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
from services.skills import build_skill_entries, get_active_tools, get_skill
from services.volume import (
    listdir_async,
    read_volume_file_async,
    reload_volume_async,
)

from observability import agent_span, bind_log_context, clear_log_context
from services.usage import record_llm_usage

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


async def _load_project_context(experiment_id: str) -> tuple[str, str, str, dict]:
    """Return (project_id, project_name, project_files_listing, sandbox_config).

    project_files_listing is a multi-line string describing all files currently
    present under /projects/{project_id}/datasets/. If the project has no data,
    returns the placeholder "(no data uploaded yet)".

    sandbox_config is the project's per-profile compute settings (default and
    training profiles, each with optional gpu + timeout). Empty dict if unset.
    """
    project_id = ""
    project_name = ""
    sandbox_config: dict = {}
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
                    sandbox_config = project.sandbox_config or {}
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

    return project_id, project_name, files_listing, sandbox_config


def _format_compute_env(sandbox_config: dict) -> str:
    """Render the project's per-profile sandbox config as a prompt block the
    agent can read before deciding how to dimension execute-code calls.

    Mirrors the runtime fallback in services/sandbox.py:
      gpu = profile.get("gpu") or None              → CPU only
      timeout = profile.get("timeout") or settings.sandbox_timeout  (default 600)
    """
    fallback_timeout = settings.sandbox_timeout

    def _profile_line(label: str, profile: dict | None, default_to_used: int) -> str:
        p = profile or {}
        gpu = p.get("gpu")
        timeout = p.get("timeout") or fallback_timeout
        gpu_part = f"GPU={gpu}" if gpu else "CPU only (no GPU)"
        timeout_part = f"timeout={timeout}s ({timeout // 60}m{timeout % 60:02d}s)"
        return f"  - **{label}**: {gpu_part}, {timeout_part}"

    default_profile = sandbox_config.get("default")
    training_profile = sandbox_config.get("training")

    lines = [
        "## Compute environment for `execute-code`",
        "",
        "Your sandbox is provisioned per call by Modal. Two profiles are",
        "configured at the project level — pick the right one when you call",
        "the skill:",
        "",
        _profile_line(
            "default profile (`heavy=False`, the default)", default_profile, 600
        ),
        _profile_line("training profile (`heavy=True`)", training_profile, 1800),
        "",
        "Dimension your code to fit:",
        "- **Timeout is per call**, not per session. If a single fit / sweep",
        "  would exceed it, split the work across multiple calls (one fold,",
        "  one trial, one epoch chunk per call) and persist intermediate",
        "  state to the session workspace between calls.",
        "- **No GPU configured for a profile** → don't import torch.cuda or",
        "  rely on `device='cuda'`. Stay on CPU-friendly libraries (xgboost,",
        "  lightgbm, sklearn) or use small models.",
        "- **GPU configured** → free to use torch / GPU-accelerated paths.",
        "  Match batch size and model size to the GPU's memory class.",
        "- Use `heavy=True` when calling `execute-code` for any work that",
        "  needs the training profile (long-running fit, hyperparameter sweep,",
        "  GPU-bound code). The default profile is for inspection / quick checks.",
        "- The user can change these settings live in the Project Settings",
        "  modal — your next call will pick up the new values automatically.",
    ]
    return "\n".join(lines)


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


def _resolve_effective_skills(
    *, base_skills: list[str], session_id: str, agent_id: str
) -> list[str]:
    """Union the agent's base skills with capability skills activated via use-skill.

    Capability skills are activated by knowledge skills declaring
    `enables: [<slug>...]` in their frontmatter and being loaded through the
    `use-skill` tool. Activations are scoped to (session_id, agent_id) and
    cleared on session cleanup.

    Returns base skill order first, then activations appended in registry
    order (deterministic for prompt-cache stability).
    """
    base_set = set(base_skills)
    extras: list[str] = []
    for slug in sorted(get_active_tools(session_id, agent_id)):
        if slug in base_set:
            continue
        try:
            skill = get_skill(slug)
        except KeyError:
            continue
        if skill.has_handler:
            extras.append(slug)
    return list(base_skills) + extras


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
    thinking_level: str | None = None,
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

    base_agent_skills = list(agent_skills)

    def _build_skill_entries_for(skills: list[str]) -> dict:
        """Closure to rebuild handlers for a given skill list — used per-turn
        in the non-Claude path so newly-activated tools become callable."""
        return build_skill_entries(
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
            agent_skills_override=skills,
        )

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

    # Wall-clock cap hint for providers/SDKs. The runner no longer wraps its
    # own loop with `asyncio.timeout(timeout_s)` — that competed with the
    # per-sandbox timeout configured per project and could kill a session
    # mid-tool-call without surfacing the failure to the model. The single
    # governing timeout is the sandbox's own (`sandbox_timeout`, override
    # per project via the agent's `default`/`training` profile). When it
    # fires, Modal kills the container and the execute-code handler returns
    # an `is_error` tool_result so the model can recognise the timeout and
    # adapt (smaller chunk, different approach) or stop. The value below is
    # still passed as a hint to provider SDKs that accept one.
    timeout_s = settings.agent_timeout_seconds

    # Translate the resolved thinking level into provider-shaped kwargs once
    # so both run-paths spread the same config. OpenAI consumes
    # `reasoning_effort`; Claude/Gemini accept the kwargs and currently
    # ignore them (extending those providers is tracked separately).
    thinking_kwargs: dict = {}
    if thinking_level:
        try:
            from services.llm.thinking import to_provider_config

            thinking_kwargs = to_provider_config(
                provider_id, thinking_level, model_id=model
            )
        except Exception as e:
            logger.debug("thinking config build failed: %s", e)

    # ---- Claude / MCP path -------------------------------------------------
    if caps.supports_mcp:
        # claude-agent-sdk's query() bakes the toolset/MCP server in upfront
        # and runs the multi-turn loop internally — we can't add tools mid-
        # conversation. Snapshot the active set at run start so sub-agents
        # (each their own provider.run() call) inherit any tools their parent
        # activated; within a single Claude run the toolset stays fixed.
        claude_skills = _resolve_effective_skills(
            base_skills=base_agent_skills,
            session_id=session_id,
            agent_id=agent_id,
        )
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
            agent_skills_override=claude_skills,
        )
        prefixed_tool_names = [f"mcp__trainable__{s}" for s in claude_skills]

        async for event in provider.run(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            tools=prefixed_tool_names,
            mcp_servers={"trainable": mcp_server},
            max_turns=settings.agent_max_turns,
            timeout_seconds=timeout_s,
            env={"CLAUDE_CODE_OAUTH_TOKEN": settings.claude_code_oauth_token},
            **thinking_kwargs,
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
                # Partial events are per-AssistantMessage deltas the
                # provider emits for live cost feedback; don't write
                # a DB row for those (would double-count against the
                # final ResultMessage aggregate). Final events have
                # `partial=False` (default) and are recorded.
                if event.data.get("partial"):
                    continue
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
    # OpenAI/LiteLLM/Gemini path. Each provider translates this Chat-
    # Completions-shaped `messages` list into its native conversation
    # shape, so the runner can re-inject tool results without caring
    # which SDK is downstream.
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    # Cache last-resolved skill set so we only rebuild specs/handlers when
    # use-skill expanded the active set. Stable across turns when nothing
    # changed — keeps tool_use_id continuity in the provider.
    _cached_skills: list[str] | None = None
    skill_specs: list[dict] = []
    skill_handlers: dict = {}

    for turn in range(settings.agent_max_turns):
        effective_skills = _resolve_effective_skills(
            base_skills=base_agent_skills,
            session_id=session_id,
            agent_id=agent_id,
        )
        if effective_skills != _cached_skills:
            skill_specs = _record_skill_specs(agent_type, effective_skills)
            entries = _build_skill_entries_for(effective_skills)
            skill_handlers = {slug: e["handler"] for slug, e in entries.items()}
            _cached_skills = effective_skills

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
            **thinking_kwargs,
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
                # Opaque per-provider continuation metadata (e.g. Gemini
                # 3's thought_signature). The runner doesn't read it —
                # we just preserve it on the assistant message so the
                # provider can restore it on the next turn.
                pmeta = event.data.get("provider_metadata")
                pending_calls.append(
                    {"id": call_id, "name": name, "args": args, "pmeta": pmeta}
                )
                await _persist_tool_call(name, call_id, args)
            elif event.kind == "usage":
                # Partial events are per-AssistantMessage deltas the
                # provider emits for live cost feedback; don't write
                # a DB row for those (would double-count against the
                # final ResultMessage aggregate). Final events have
                # `partial=False` (default) and are recorded.
                if event.data.get("partial"):
                    continue
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
                    # `_provider_metadata` is namespaced with an
                    # underscore so providers that don't use it (OpenAI,
                    # LiteLLM) can ignore the unknown key safely.
                    **(
                        {"_provider_metadata": c["pmeta"]} if c.get("pmeta") else {}
                    ),
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

    bind_log_context(
        session_id=session_id,
        agent_type=agent_type,
        agent_id=agent_id,
        depth=depth,
    )

    try:
        prev_context = await _load_prev_context(session_id, stage)
        (
            project_id,
            project_name,
            project_files,
            sandbox_config,
        ) = await _load_project_context(experiment_id)

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

        # Per-project compute env (GPU + timeout per profile) so the agent can
        # dimension execute-code calls — split long fits, skip CUDA on CPU
        # profiles, use heavy=True for the training profile, etc. Only useful
        # for agents that can actually call execute-code; others ignore it.
        if "execute-code" in get_agent_skills(agent_type):
            system_prompt += "\n\n" + _format_compute_env(sandbox_config)

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
            thinking_level=thinking_level,
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
        # Defense in depth. The runner no longer wraps its own loop with an
        # outer timeout — the sandbox's own (per-project-profile) timeout is
        # the single governing cap, and execute-code surfaces it as a
        # tool_output. Reaching here means a provider SDK itself raised
        # TimeoutError (e.g. network stall). Persist a clear note so the
        # user can resume rather than losing the session.
        logger.warning(
            "Provider raised TimeoutError for agent %s session %s",
            agent_type,
            session_id,
        )
        await _publish(
            "agent_timeout",
            {
                "error": (
                    "Provider call timed out at the SDK level. The "
                    "conversation history is preserved — send a new message "
                    "to continue."
                )
            },
            role="system",
        )
        await _publish("state_change", {"state": "timed_out"}, role="system")

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
