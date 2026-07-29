"""Agent catalog — loads agent definitions from YAML files in agents/ directory.

Skills (capability + knowledge) live under backend/skills/<slug>/. An agent's
YAML lists which capability skills it can call. The skill description and
input_schema are sourced from the skill's SKILL.md frontmatter and schema.yaml.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

from services.skills import get_skill

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


def _agent_skills_raw(agent_type: str) -> list:
    """Return the raw `skills:` list from an agent's YAML.

    Defaults to a single execute-code skill so legacy YAMLs without a
    skills field still produce a runnable agent.
    """
    cfg = get_agent(agent_type)
    return cfg.get("skills") or [{"name": "execute-code"}]


def get_agent_skills(agent_type: str) -> list[str]:
    """Get the list of skill slugs available to this agent."""
    raw = _agent_skills_raw(agent_type)
    names: list[str] = []
    for s in raw:
        if isinstance(s, str):
            names.append(s)
        elif isinstance(s, dict):
            names.append(s["name"])
    return names


def get_agent_skill_configs(agent_type: str) -> list[dict]:
    """Get the full skill config list (with optional per-agent description / schema overrides)."""
    raw = _agent_skills_raw(agent_type)
    configs = []
    for s in raw:
        if isinstance(s, str):
            configs.append({"name": s})
        elif isinstance(s, dict):
            configs.append(s)
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


def get_agent_provider(agent_type: str) -> str:
    """Return the LLM provider id for this agent. Defaults to 'claude' so
    legacy YAMLs without a provider field continue to work unchanged."""
    return get_agent(agent_type).get("provider", "claude")


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
            result.append(
                {
                    "type": agent_type,
                    "description": config.get("description", ""),
                    "default_model": config.get("default_model", "claude-sonnet-4-6"),
                }
            )
        except KeyError:
            logger.warning(
                "Subagent '%s' referenced by '%s' not found", agent_type, from_agent
            )
    return result


def can_delegate(agent_type: str, current_depth: int) -> bool:
    """Check if an agent can delegate at the given depth."""
    max_depth = get_agent_max_depth(agent_type)
    has_subagents = len(get_agent_subagents(agent_type)) > 0
    return has_subagents and current_depth < max_depth


def get_skill_default(skill_slug: str) -> dict:
    """Get a skill's default description and input_schema from the registry.

    Returns {description, input_schema} pulled from the skill's SKILL.md
    frontmatter and schema.yaml.
    """
    try:
        skill = get_skill(skill_slug)
    except KeyError:
        return {"description": "", "input_schema": {}}
    return {
        "description": skill.description,
        "input_schema": skill.schema,
    }


def get_skill_for_agent(agent_type: str, skill_slug: str) -> dict:
    """Merge a skill's defaults with any per-agent override.

    Agent YAML can override description and/or input_schema per skill:
      skills:
        - name: execute-code
          description: "Custom description for this agent..."
          input_schema: { ... }  # optional override
    """
    defaults = get_skill_default(skill_slug)
    agent_configs = get_agent_skill_configs(agent_type)

    override: dict = {}
    for sc in agent_configs:
        if sc.get("name") == skill_slug:
            override = sc
            break

    return {
        "name": skill_slug,
        "description": override.get("description", defaults.get("description", "")),
        "input_schema": override.get("input_schema", defaults.get("input_schema", {})),
    }


def render_skill_description(
    *,
    skill_slug: str = "execute-code",
    agent_type: str = "eda",
    experiment_id: str,
    session_id: str,
    stage: str,
) -> str:
    """Render a skill's description with placeholders filled, respecting per-agent overrides."""
    skill_config = get_skill_for_agent(agent_type, skill_slug)
    template = skill_config.get("description", "")
    return (
        template.replace("{experiment_id}", experiment_id)
        .replace("{session_id}", session_id)
        .replace("{stage}", stage)
    )


def get_skill_input_schema(
    skill_slug: str = "execute-code",
    agent_type: str = "eda",
) -> dict:
    """Get a skill's input_schema, respecting per-agent overrides."""
    skill_config = get_skill_for_agent(agent_type, skill_slug)
    return skill_config.get("input_schema", {})


def list_all_agents() -> list[dict]:
    """List all available agents with their metadata."""
    agents = []
    for agent_type in _discover_agents():
        try:
            config = get_agent(agent_type)
            agents.append(
                {
                    "type": agent_type,
                    "name": config.get("name", agent_type),
                    "description": config.get("description", ""),
                    "default_model": config.get("default_model", ""),
                    "max_depth": config.get("max_depth", 0),
                    "skills": config.get("skills", []),
                    "subagents": config.get("subagents", []),
                    "provider": config.get("provider", "claude"),
                }
            )
        except Exception as e:
            logger.warning("Failed to load agent '%s': %s", agent_type, e)
    return agents
