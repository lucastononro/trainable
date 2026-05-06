"""MCP-server bridge — builds an MCP server from capability skills.

This is the Claude-specific runtime adapter. The Claude provider passes the
returned MCP server to claude-agent-sdk. Non-Claude providers don't use this;
they call `build_skill_handlers()` to get a {slug: callable} dict and dispatch
tool_call events themselves in the runner loop.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .registry import get_skill, load_handler

logger = logging.getLogger(__name__)


def _agents():
    """Lazy import to avoid a circular dependency with services.agent."""
    from services.agent import agents as a

    return a


def _create_mcp_server():
    from services.mcp_tools import create_trainable_mcp_server

    return create_trainable_mcp_server


def _instantiate_handler(
    slug: str,
    *,
    agent_type: str,
    session_id: str,
    experiment_id: str,
    stage: str,
    depth: int,
    publish_fn,
    agent_id: str,
    parent_agent_id: str | None,
    sandbox_config: dict,
    model: str | None,
    instructions: str,
    agent_models: dict,
) -> Callable | None:
    """Load a capability skill's handler factory and bind it to the run context."""
    try:
        create_handler = load_handler(slug)
    except (KeyError, ValueError, AttributeError, ImportError) as e:
        logger.warning("Skill '%s' handler unavailable: %s", slug, e)
        return None

    return create_handler(
        session_id=session_id,
        experiment_id=experiment_id,
        stage=stage,
        publish_fn=publish_fn,
        parent_agent_type=agent_type,
        parent_agent_id=agent_id,
        parent_parent_agent_id=parent_agent_id,
        current_depth=depth,
        sandbox_config=sandbox_config,
        parent_model=model,
        instructions=instructions,
        agent_models=agent_models,
    )


def build_skill_entries(
    *,
    agent_type: str,
    session_id: str,
    experiment_id: str,
    stage: str,
    depth: int,
    publish_fn,
    sandbox_config: dict | None = None,
    model: str | None = None,
    instructions: str = "",
    agent_models: dict | None = None,
    agent_id: str = "root",
    parent_agent_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build {slug: {description, input_schema, handler}} for an agent's skills.

    Used by both the MCP bridge (Claude) and the runner's non-MCP tool loop
    (OpenAI/Gemini/LiteLLM).
    """
    agents_mod = _agents()
    skill_slugs = agents_mod.get_agent_skills(agent_type)
    entries: dict[str, dict[str, Any]] = {}

    for slug in skill_slugs:
        # delegate-task is gated on whether the agent has remaining depth.
        if slug == "delegate-task" and not agents_mod.can_delegate(agent_type, depth):
            continue

        try:
            skill = get_skill(slug)
        except KeyError:
            logger.warning(
                "Unknown skill '%s' referenced by agent '%s'", slug, agent_type
            )
            continue
        if not skill.has_handler:
            logger.warning(
                "Skill '%s' has no handler — cannot expose as capability", slug
            )
            continue

        handler = _instantiate_handler(
            slug,
            agent_type=agent_type,
            session_id=session_id,
            experiment_id=experiment_id,
            stage=stage,
            depth=depth,
            publish_fn=publish_fn,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            sandbox_config=sandbox_config or {},
            model=model,
            instructions=instructions,
            agent_models=agent_models or {},
        )
        if handler is None:
            continue

        description = agents_mod.render_skill_description(
            skill_slug=slug,
            agent_type=agent_type,
            experiment_id=experiment_id,
            session_id=session_id,
            stage=stage,
        )
        input_schema = agents_mod.get_skill_input_schema(slug, agent_type)

        # delegate-task description gets dynamically suffixed with the list of
        # subagents the parent can spawn, and the agent_type enum is injected.
        if slug == "delegate-task":
            agents_info = agents_mod.list_delegatable_agents(agent_type)
            agent_types = [a["type"] for a in agents_info]
            agents_desc = "\n".join(
                f"- {a['type']}: {a['description']}" for a in agents_info
            )
            description = description.rstrip() + "\n\nAvailable agents:\n" + agents_desc
            if (
                "properties" in input_schema
                and "agent_type" in input_schema["properties"]
            ):
                input_schema["properties"]["agent_type"]["enum"] = agent_types

        entries[slug] = {
            "description": description,
            "input_schema": input_schema,
            "handler": handler,
        }

    return entries


def build_mcp_server(
    *,
    agent_type: str,
    session_id: str,
    experiment_id: str,
    stage: str,
    depth: int = 0,
    publish_fn,
    sandbox_config: dict | None = None,
    model: str | None = None,
    instructions: str = "",
    agent_models: dict | None = None,
    agent_id: str = "root",
    parent_agent_id: str | None = None,
):
    """Build an MCP server with all capability skills assigned to this agent.

    The Claude provider consumes the returned MCP server. Non-Claude providers
    use `build_skill_entries()` instead and dispatch tool calls in the runner.
    """
    entries = build_skill_entries(
        agent_type=agent_type,
        session_id=session_id,
        experiment_id=experiment_id,
        stage=stage,
        depth=depth,
        publish_fn=publish_fn,
        sandbox_config=sandbox_config,
        model=model,
        instructions=instructions,
        agent_models=agent_models,
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
    )
    logger.debug(
        "Built MCP server for agent=%s with skills=%s",
        agent_type,
        list(entries.keys()),
    )
    return _create_mcp_server()(entries)
