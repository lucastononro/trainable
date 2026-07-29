"""list_available_skills tool — return the skill catalog."""

from __future__ import annotations

import json

from services.skills import list_skills


def create_handler(**_):
    async def handler(_args: dict):
        catalog = list_skills()
        if not catalog:
            return {"content": [{"type": "text", "text": "No skills installed."}]}
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"skills": catalog}, indent=2),
                }
            ]
        }

    return handler
