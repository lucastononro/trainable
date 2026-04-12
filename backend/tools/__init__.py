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
        agents_desc = "\n".join(f"- {a['type']}: {a['description']}" for a in agents_info)
        description = description.rstrip() + "\n\nAvailable agents:\n" + agents_desc

        if "properties" in input_schema and "agent_type" in input_schema["properties"]:
            input_schema["properties"]["agent_type"]["enum"] = agent_types

    return {
        "description": description,
        "input_schema": input_schema,
        "handler": handler,
    }


def build_mcp_server(
    *,
    agent_type: str,
    session_id: str,
    experiment_id: str,
    stage: str,
    depth: int = 0,
    publish_fn,
    gpu: str | None = None,
    model: str | None = None,
    instructions: str = "",
    agent_models: dict | None = None,
):
    """Build an MCP server with all tools defined in the agent's YAML config.

    Reads the agent's `tools` list, loads each tool's YAML + Python handler,
    and assembles them into an MCP server ready for Claude Agent SDK.
    """
    agent_tools = _agents().get_agent_tools(agent_type)
    tool_entries = {}

    for tool_name in agent_tools:
        entry = _build_tool_entry(
            tool_name,
            agent_type=agent_type,
            session_id=session_id,
            experiment_id=experiment_id,
            stage=stage,
            depth=depth,
            publish_fn=publish_fn,
            gpu=gpu,
            parent_model=model,
            instructions=instructions,
            agent_models=agent_models or {},
        )
        if entry:
            tool_entries[tool_name] = entry

    logger.debug(
        "Built MCP server for agent=%s with tools=%s",
        agent_type, list(tool_entries.keys()),
    )

    return _mcp()(tool_entries)
