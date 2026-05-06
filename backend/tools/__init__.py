"""Tool registry — auto-discovers tools from this directory and builds MCP servers.

Each tool is a pair of files:
  - <name>.yaml  — default description + input_schema (can be overridden per-agent)
  - <name>.py    — exports create_handler(**context) → async handler(args)

Usage:
  from tools import build_mcp_server
  mcp = build_mcp_server(agent_type="eda", session_id=..., ...)
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _agents():
    """Lazy import to avoid circular dependency with services.agent."""
    from services.agent import agents as a

    return a


def _mcp():
    """Lazy import."""
    from services.mcp_tools import create_trainable_mcp_server

    return create_trainable_mcp_server


_TOOLS_DIR = Path(__file__).parent


def _load_tool_module(tool_name: str):
    """Dynamically import a tool's Python module."""
    return importlib.import_module(f"tools.{tool_name}")


def _build_tool_entry(
    tool_name: str,
    *,
    agent_type: str,
    session_id: str,
    experiment_id: str,
    stage: str,
    depth: int,
    publish_fn,
    agent_id: str = "root",
    parent_agent_id: str | None = None,
    **handler_kwargs,
) -> dict | None:
    """Build a single tool dict {description, input_schema, handler} for the MCP server."""

    # Special check: delegate_task only available if agent can delegate at this depth
    if tool_name == "delegate_task" and not _agents().can_delegate(agent_type, depth):
        return None

    # Load the handler module
    module_path = _TOOLS_DIR / f"{tool_name}.py"
    if not module_path.exists():
        logger.warning("Tool handler not found: %s", module_path)
        return None

    mod = _load_tool_module(tool_name)
    if not hasattr(mod, "create_handler"):
        logger.warning("Tool module %s missing create_handler()", tool_name)
        return None

    # Create the handler with full context
    handler = mod.create_handler(
        session_id=session_id,
        experiment_id=experiment_id,
        stage=stage,
        publish_fn=publish_fn,
        parent_agent_type=agent_type,
        parent_agent_id=agent_id,
        parent_parent_agent_id=parent_agent_id,
        current_depth=depth,
        **handler_kwargs,
    )

    # Load description from YAML (with per-agent override)
    description = _agents().render_tool_description(
        tool_name=tool_name,
        agent_type=agent_type,
        experiment_id=experiment_id,
        session_id=session_id,
        stage=stage,
    )

    # Load input_schema from YAML (with per-agent override)
    input_schema = _agents().get_tool_input_schema(tool_name, agent_type)

    # For delegate_task: append available agents and inject enum
    if tool_name == "delegate_task":
        agents_info = _agents().list_delegatable_agents(agent_type)
        agent_types = [a["type"] for a in agents_info]
        agents_desc = "\n".join(
            f"- {a['type']}: {a['description']}" for a in agents_info
        )
        description = description.rstrip() + "\n\nAvailable agents:\n" + agents_desc

        if "properties" in input_schema and "agent_type" in input_schema["properties"]:
            input_schema["properties"]["agent_type"]["enum"] = agent_types

    return {
        "description": description,
        "input_schema": input_schema,
        "handler": handler,
    }


def build_tool_entries(
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
    agent_thinking: dict | None = None,
    agent_id: str = "root",
    parent_agent_id: str | None = None,
) -> dict[str, dict]:
    """Resolve {tool_name: {description, input_schema, handler}} for an agent.

    Backbone for both:
      - the Claude OAuth path (wraps these in an MCP server below), and
      - the native-SDK path (exposes the handlers directly to a runner-
        side tool-dispatch loop, since OpenAI/Gemini don't speak MCP).

    Same context kwargs as ``build_mcp_server`` for symmetry.
    """
    agent_tools = _agents().get_agent_tools(agent_type)
    tool_entries: dict[str, dict] = {}

    for tool_name in agent_tools:
        entry = _build_tool_entry(
            tool_name,
            agent_type=agent_type,
            session_id=session_id,
            experiment_id=experiment_id,
            stage=stage,
            depth=depth,
            publish_fn=publish_fn,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            sandbox_config=sandbox_config or {},
            parent_model=model,
            instructions=instructions,
            agent_models=agent_models or {},
            agent_thinking=agent_thinking or {},
        )
        if entry:
            tool_entries[tool_name] = entry

    logger.debug(
        "Built tool entries for agent=%s tools=%s",
        agent_type,
        list(tool_entries.keys()),
    )
    return tool_entries


def build_mcp_server(**kwargs):
    """Build an MCP server for the Claude OAuth (claude-agent-sdk) path.

    Thin wrapper over ``build_tool_entries`` — the entries dict is the
    portable surface; MCP is just one transport for it.
    """
    tool_entries = build_tool_entries(**kwargs)
    return _mcp()(tool_entries)


def to_provider_specs(tool_entries: dict[str, dict]) -> list[dict]:
    """Project ``build_tool_entries`` output to provider-neutral tool specs.

    Returns ``[{name, description, input_schema}]`` — the shape the
    OpenAI / Gemini providers' ``tools=`` parameter expects. Drops the
    handler (callers dispatch through ``tool_entries[name]['handler']``).
    """
    return [
        {
            "name": name,
            "description": entry.get("description", ""),
            "input_schema": entry.get("input_schema") or {"type": "object", "properties": {}},
        }
        for name, entry in tool_entries.items()
    ]
