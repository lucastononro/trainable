"""Agent catalog — loads agent definitions from YAML files in agents/ directory."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"


@lru_cache(maxsize=None)
def _load_agent_yaml(agent_type: str) -> dict:
    """Load a single agent YAML file."""
    path = _AGENTS_DIR / f"{agent_type}.yaml"
    if not path.exists():
        raise KeyError(f"Agent definition not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def _discover_agents() -> list[str]:
    """Discover all agent types from YAML files in the agents/ directory."""
    if not _AGENTS_DIR.exists():
        logger.warning("Agents directory not found: %s", _AGENTS_DIR)
        return []
    return [p.stem for p in _AGENTS_DIR.glob("*.yaml")]


def get_agent(agent_type: str) -> dict:
    """Get full agent config by type. Raises KeyError if not found."""
    try:
        return _load_agent_yaml(agent_type)
    except KeyError:
        valid = ", ".join(_discover_agents())
        raise KeyError(f"Unknown agent type '{agent_type}'. Valid types: {valid}")


def get_agent_system_prompt(agent_type: str) -> str:
    """Get the system prompt template for an agent."""
    return get_agent(agent_type)["system"]


def get_agent_opener(agent_type: str) -> str:
    """Get the opening message for an agent."""
    return get_agent(agent_type).get("opener", "")


def get_agent_tools(agent_type: str) -> list[str]:
    """Get the list of tool names available to this agent."""
    raw = get_agent(agent_type).get("tools", [{"name": "execute_code"}])
    names = []
    for t in raw:
        if isinstance(t, str):
            names.append(t)
        elif isinstance(t, dict):
            names.append(t["name"])
    return names


def get_agent_tool_configs(agent_type: str) -> list[dict]:
    """Get the full tool config list (with optional per-agent description/schema overrides)."""
    raw = get_agent(agent_type).get("tools", [{"name": "execute_code"}])
    configs = []
    for t in raw:
        if isinstance(t, str):
            configs.append({"name": t})
        elif isinstance(t, dict):
            configs.append(t)
    return configs


def get_agent_subagents(agent_type: str) -> list[str]:
    """Get the list of agent types this agent can delegate to."""
    return get_agent(agent_type).get("subagents", [])


def get_agent_max_depth(agent_type: str) -> int:
    """Get the maximum delegation depth for this agent. 0 = leaf (no delegation)."""
    return get_agent(agent_type).get("max_depth", 0)


def get_agent_default_model(agent_type: str) -> str:
    """Get the default model for this agent."""
    return get_agent(agent_type).get("default_model", "claude-sonnet-4-6")


def render_agent_system_prompt(
    agent_type: str,
    *,
    experiment_id: str,
    session_id: str,
    instructions: str = "",
    prev_context: str = "(No previous context available)",
    project_id: str = "",
    project_name: str = "",
    project_files: str = "(no data uploaded yet)",
) -> str:
    """Load an agent's system prompt template and fill in placeholders."""
    template = get_agent_system_prompt(agent_type)
    return (
        template.replace("{experiment_id}", experiment_id)
        .replace("{session_id}", session_id)
        .replace("{project_id}", project_id)
        .replace("{project_name}", project_name)
        .replace("{project_files}", project_files)
        .replace("{instructions}", instructions or "No specific instructions.")
        .replace("{prev_context}", prev_context)
    )


def list_delegatable_agents(from_agent: str) -> list[dict]:
    """Return descriptions of agents that `from_agent` can delegate to."""
    allowed = get_agent_subagents(from_agent)
    result = []
    for agent_type in allowed:
        try:
            config = get_agent(agent_type)
            result.append({
                "type": agent_type,
                "description": config.get("description", ""),
                "default_model": config.get("default_model", "claude-sonnet-4-6"),
            })
        except KeyError:
            logger.warning("Subagent '%s' referenced by '%s' not found", agent_type, from_agent)
    return result


def can_delegate(agent_type: str, current_depth: int) -> bool:
    """Check if an agent can delegate at the given depth."""
    max_depth = get_agent_max_depth(agent_type)
    has_subagents = len(get_agent_subagents(agent_type)) > 0
    return has_subagents and current_depth < max_depth


_TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"


@lru_cache(maxsize=None)
def _load_tool_yaml(tool_name: str) -> dict:
    """Load a tool's default YAML definition."""
    path = _TOOLS_DIR / f"{tool_name}.yaml"
    if not path.exists():
        return {"name": tool_name, "description": tool_name, "input_schema": {}}
    with open(path) as f:
        return yaml.safe_load(f)


def get_tool_default(tool_name: str) -> dict:
    """Get a tool's default description and input_schema from tools/*.yaml."""
    return _load_tool_yaml(tool_name)


def get_tool_for_agent(agent_type: str, tool_name: str) -> dict:
    """Get a tool config for a specific agent, merging per-agent overrides with defaults.

    Agent YAML can override description and/or input_schema per tool:
      tools:
        - name: execute_code
          description: "Custom description for this agent..."
          input_schema: { ... }  # optional override
    """
    defaults = get_tool_default(tool_name)
    agent_configs = get_agent_tool_configs(agent_type)

    # Find per-agent override for this tool
    override = {}
    for tc in agent_configs:
        if tc.get("name") == tool_name:
            override = tc
            break

    return {
        "name": tool_name,
        "description": override.get("description", defaults.get("description", "")),
        "input_schema": override.get("input_schema", defaults.get("input_schema", {})),
    }


def render_tool_description(
    *,
    tool_name: str = "execute_code",
    agent_type: str = "eda",
    experiment_id: str,
    session_id: str,
    stage: str,
) -> str:
    """Render a tool's description with placeholders filled, respecting per-agent overrides."""
    tool_config = get_tool_for_agent(agent_type, tool_name)
    template = tool_config.get("description", "")
    return (
        template.replace("{experiment_id}", experiment_id)
        .replace("{session_id}", session_id)
        .replace("{stage}", stage)
    )


def get_tool_input_schema(
    tool_name: str = "execute_code",
    agent_type: str = "eda",
) -> dict:
    """Get a tool's input_schema, respecting per-agent overrides."""
    tool_config = get_tool_for_agent(agent_type, tool_name)
    return tool_config.get("input_schema", {})


def list_all_agents() -> list[dict]:
    """List all available agents with their metadata."""
    agents = []
    for agent_type in _discover_agents():
        try:
            config = get_agent(agent_type)
            agents.append({
                "type": agent_type,
                "name": config.get("name", agent_type),
                "description": config.get("description", ""),
                "default_model": config.get("default_model", ""),
                "max_depth": config.get("max_depth", 0),
                "tools": config.get("tools", []),
                "subagents": config.get("subagents", []),
            })
        except Exception as e:
            logger.warning("Failed to load agent '%s': %s", agent_type, e)
    return agents
