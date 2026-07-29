"""Skill catalog endpoints — surface what's installed to the frontend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.skills import list_skills, load_skill

router = APIRouter()


@router.get("/skills")
async def get_skills():
    """List all installed skills (lightweight catalog)."""
    return list_skills()


@router.get("/skills/{slug}")
async def get_skill(slug: str):
    """Return a single skill's full body + file manifest."""
    try:
        return load_skill(slug)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found")
