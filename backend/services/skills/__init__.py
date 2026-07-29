"""Skills — unified capability + knowledge surface.

A skill lives at backend/skills/<slug>/ with:
  - SKILL.md (required)        frontmatter (name, description, when_to_use,
                               version, kind?) + markdown body
  - handler.py (optional)      exports create_handler(**context) -> async handler(args)
                               When present, the skill is a CAPABILITY (callable).
  - schema.yaml (optional)     JSON-schema for the handler's args. Required when
                               handler.py is present.
  - scripts/, templates/ ...   Files mounted to sandbox at /skills/<slug>/

Three shapes:
  capability   handler.py + schema.yaml present (replaces the old `tools/` modules)
  knowledge    only SKILL.md (loaded via the `use-skill` capability skill)
  hybrid       handler + body + scripts

The registry is provider-neutral. Each provider translates capability skills
into its native function-calling protocol (MCP for Claude, OpenAI tools for
OpenAI/LiteLLM, function_declarations for Gemini).
"""

from __future__ import annotations

from .mcp_bridge import build_mcp_server, build_skill_entries
from .registry import (
    Skill,
    discover_skills,
    get_capability_skills,
    get_knowledge_skills,
    get_skill,
    get_normalized_specs,
    list_skills,
    load_handler,
    load_skill,
)
from .state import activate_tools, get_active_tools

__all__ = [
    "Skill",
    "activate_tools",
    "build_mcp_server",
    "build_skill_entries",
    "discover_skills",
    "get_active_tools",
    "get_capability_skills",
    "get_knowledge_skills",
    "get_skill",
    "get_normalized_specs",
    "list_skills",
    "load_handler",
    "load_skill",
]
