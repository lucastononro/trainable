"""Model and agent catalog endpoints.

Model metadata + pricing live in `backend/services/llm/models.yml`
(single source of truth, colocated with the provider code that
consumes it). This router projects the catalog into the JSON shape
the frontend's model-picker expects.
"""

import os

from fastapi import APIRouter

from services.agent.agents import list_all_agents
from services.llm.factory import list_providers
from services.usage import get_llm_catalog

router = APIRouter()


# Per-provider env-var rules. Resolution mirrors the providers themselves
# (claude_provider / openai_provider / gemini_provider): any one of the
# listed env vars is enough to mark the provider available. Update here
# when a new auth path lands (e.g., file-based OAuth mounts).
_PROVIDER_AUTH_ENV: dict[str, list[str]] = {
    "claude": ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
    "anthropic": ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    # LiteLLM routes to many backends — the relevant key depends on the
    # model id ("groq/<...>" → GROQ_API_KEY, "mistral/<...>" → MISTRAL_API_KEY).
    # We list common ones; resolution is "any one set is enough".
    "litellm": [
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "DEEPSEEK_API_KEY",
        "TOGETHER_API_KEY",
        "OPENROUTER_API_KEY",
    ],
}

# Which providers the agent runner can actually dispatch to today.
# `services/agent/runner.py:_drive_provider` routes through the LLM
# factory: Claude (with `supports_mcp=True`) keeps the rich MCP-based
# loop; everyone else runs a runner-managed tool-execution loop using
# native SDKs. All registered providers are dispatchable.
_PROVIDER_RUNNER_SUPPORTED: dict[str, bool] = {
    "claude": True,
    "anthropic": True,
    "openai": True,
    "gemini": True,
    "google": True,
    "litellm": True,
}


def _provider_availability(provider_id: str) -> dict:
    """Return availability info for one provider.

    Shape:
        {
          "available":        bool,    # API key/OAuth present
          "missing_env":      [str],   # env names that would enable it
          "runner_supported": bool,    # agent runner can actually dispatch
        }

    The frontend disables a model when EITHER `available` OR
    `runner_supported` is false, with a hover hint explaining which.
    """
    required = _PROVIDER_AUTH_ENV.get(provider_id, [])
    runner_supported = _PROVIDER_RUNNER_SUPPORTED.get(provider_id, False)
    if not required:
        # Unknown providers default to "available" auth-wise (nothing to
        # gate on), but the runner gate still applies.
        return {
            "available": True,
            "missing_env": [],
            "runner_supported": runner_supported,
        }
    if any(os.getenv(name) for name in required):
        return {
            "available": True,
            "missing_env": [],
            "runner_supported": runner_supported,
        }
    return {
        "available": False,
        "missing_env": required,
        "runner_supported": runner_supported,
    }


def _to_api_model(model_id: str, entry: dict) -> dict:
    """Project one services/llm/models.yml entry into the /api/models response shape.

    The frontend expects `input_cost` / `output_cost` (USD/M tokens) on top
    of the existing display fields. We don't expose `cache_read` /
    `cache_creation` here yet — the picker doesn't render them. Add to the
    response when there's a UI for it.

    `thinking` is included only when the model declares one — the frontend
    treats its absence as "this model doesn't reason" and hides the picker.
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

    thinking = entry.get("thinking")
    if isinstance(thinking, dict):
        levels = thinking.get("levels") or []
        default = thinking.get("default")
        if isinstance(levels, list) and levels:
            out["thinking"] = {
                "default": default if default in levels else levels[0],
                "levels": [str(lvl) for lvl in levels],
            }
    return out


@router.get("/models")
async def get_models():
    """Catalog of LLM models the picker can offer. Sourced from services/llm/models.yml."""
    return [_to_api_model(mid, entry) for mid, entry in get_llm_catalog().items()]


@router.get("/providers")
async def get_providers():
    """List active LLM providers and whether each has credentials configured.

    The frontend disables models from `available: false` providers in the
    picker and surfaces `missing_env` as a hover hint so the user knows
    which env var to set in `.env`.
    """
    out: list[dict] = []
    for p in list_providers():
        out.append({"id": p, **_provider_availability(p)})
    return out


@router.get("/agents")
async def get_agents():
    """List all available agents with their configuration."""
    return list_all_agents()
