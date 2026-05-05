"""Model and agent catalog endpoints.

Model metadata + pricing live in `backend/pricing.yaml` (single source of
truth). This router projects the catalog into the JSON shape the frontend's
model-picker expects.
"""

from fastapi import APIRouter

from services.agent.agents import list_all_agents
from services.llm.factory import list_providers
from services.usage import get_llm_catalog

router = APIRouter()


def _to_api_model(model_id: str, entry: dict) -> dict:
    """Project one pricing.yaml LLM entry into the /api/models response shape.

    The frontend expects `input_cost` / `output_cost` (USD/M tokens) on top
    of the existing display fields. We don't expose `cache_read` /
    `cache_creation` here yet — the picker doesn't render them. Add to the
    response when there's a UI for it.
    """
    out: dict = {
        "id": model_id,
        "provider": entry.get("provider", "claude"),
        "name": entry.get("name", model_id),
        "tier": entry.get("tier"),
        "context": entry.get("context"),
        "input_cost": float(entry.get("input", 0) or 0),
        "output_cost": float(entry.get("output", 0) or 0),
        "description": entry.get("description", ""),
    }
    if entry.get("experimental"):
        out["experimental"] = True
    return out


@router.get("/models")
async def get_models():
    """Catalog of LLM models the picker can offer. Sourced from pricing.yaml."""
    return [_to_api_model(mid, entry) for mid, entry in get_llm_catalog().items()]


@router.get("/providers")
async def get_providers():
    """List active LLM providers — frontend uses this to gate non-default models."""
    return [{"id": p} for p in list_providers()]


@router.get("/agents")
async def get_agents():
    """List all available agents with their configuration."""
    return list_all_agents()
