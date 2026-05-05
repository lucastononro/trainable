"""use_skill tool — load a skill's body + file manifest."""

from __future__ import annotations

import json

from services.skills import load_skill


def create_handler(**_):
    async def handler(args: dict):
        slug = (args or {}).get("slug", "").strip()
        if not slug:
            return {
                "content": [
                    {"type": "text", "text": "Missing required arg: slug"}
                ],
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

        # Render body + manifest as a single text block. Keeping them in one
        # response means the agent has everything in context after one call.
        files_block = json.dumps(skill["files"], indent=2)
        text = (
            f"# Skill: {skill['name']} (slug={skill['slug']}, version={skill['version']})\n"
            f"{skill['description']}\n\n"
            f"## Instructions (SKILL.md body)\n\n{skill['body']}\n\n"
            f"## Bundled files (mounted at {skill['sandbox_root']})\n\n```json\n{files_block}\n```\n"
        )
        return {"content": [{"type": "text", "text": text}]}

    return handler
