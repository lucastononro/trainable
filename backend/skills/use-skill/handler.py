"""use_skill tool — load a skill's body + file manifest.

If the loaded skill declares `enables: [<slug>...]` in its frontmatter, those
capability skills are activated for this (session_id, agent_id) so the runner
includes them in subsequent turns' toolset. The activated tools are surfaced
in the response so the model knows what just became callable.
"""

from __future__ import annotations

import json

from services.skills import activate_tools, get_skill, load_skill


def create_handler(*, session_id: str = "", parent_agent_id: str | None = None, **_):
    # Active-tool activation is keyed by (session_id, agent_id). The MCP
    # bridge passes the agent's own id under `parent_agent_id` when
    # instantiating the handler — that's "the agent calling this tool",
    # which is the right scope for the active set. Fall back to "root"
    # so single-agent setups still work.
    agent_id = parent_agent_id or "root"

    async def handler(args: dict):
        slug = (args or {}).get("slug", "").strip()
        if not slug:
            return {
                "content": [{"type": "text", "text": "Missing required arg: slug"}],
                "is_error": True,
            }
        try:
            skill = load_skill(slug)
        except KeyError:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Skill '{slug}' not found. Call list_available_skills "
                            "to see installed skills."
                        ),
                    }
                ],
                "is_error": True,
            }

        # Activate any capability skills this skill brings in. Filter to
        # slugs that actually exist as capability skills so we never expose
        # a spec the runner can't dispatch.
        valid_enables: list[str] = []
        for enabled_slug in skill.get("enables") or []:
            try:
                target = get_skill(enabled_slug)
            except KeyError:
                continue
            if target.has_handler:
                valid_enables.append(enabled_slug)

        newly_added = activate_tools(session_id, agent_id, valid_enables)

        # Render body + manifest as a single text block. Keeping them in one
        # response means the agent has everything in context after one call.
        files_block = json.dumps(skill["files"], indent=2)
        text = (
            f"# Skill: {skill['name']} (slug={skill['slug']}, version={skill['version']})\n"
            f"{skill['description']}\n\n"
            f"## Instructions (SKILL.md body)\n\n{skill['body']}\n\n"
            f"## Bundled files (mounted at {skill['sandbox_root']})\n\n```json\n{files_block}\n```\n"
        )

        if valid_enables:
            already = [s for s in valid_enables if s not in newly_added]
            lines = ["", "## Tools enabled by this skill"]
            for s in valid_enables:
                marker = " (already active)" if s in already else ""
                lines.append(f"- `{s}`{marker}")
            lines.append(
                "\nThese capability skills are now callable as tools on the next turn."
            )
            text += "\n".join(lines)

        return {"content": [{"type": "text", "text": text}]}

    return handler
