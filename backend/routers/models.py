"""Model and agent catalog endpoints."""

from fastapi import APIRouter

from services.agent.agents import list_all_agents
from services.llm.factory import list_providers

router = APIRouter()

MODELS = [
    # Claude
    {
        "id": "claude-opus-4-7",
        "provider": "claude",
        "name": "Claude Opus 4.7",
        "tier": "premium",
        "context": "1M",
        "input_cost": 15,
        "output_cost": 75,
        "description": "Latest flagship model. Strongest reasoning and long-horizon planning.",
    },
    {
        "id": "claude-opus-4-6",
        "provider": "claude",
        "name": "Claude Opus 4.6",
        "tier": "premium",
        "context": "1M",
        "input_cost": 15,
        "output_cost": 75,
        "description": "Previous-generation flagship. Great for complex analysis and multi-step reasoning.",
    },
    {
        "id": "claude-sonnet-4-6",
        "provider": "claude",
        "name": "Claude Sonnet 4.6",
        "tier": "standard",
        "context": "1M",
        "input_cost": 3,
        "output_cost": 15,
        "description": "Balanced speed and intelligence. Great for everyday tasks and agentic workflows.",
    },
    {
        "id": "claude-haiku-4-5",
        "provider": "claude",
        "name": "Claude Haiku 4.5",
        "tier": "fast",
        "context": "200K",
        "input_cost": 0.80,
        "output_cost": 4,
        "description": "Fastest model. Ideal for quick iterations, code review, and high-volume tasks.",
    },
    # OpenAI — surfaced once OPENAI_API_KEY is set; the factory will accept
    # them only if the openai SDK is installed.
    {
        "id": "gpt-5",
        "provider": "openai",
        "name": "GPT-5",
        "tier": "premium",
        "context": "400K",
        "input_cost": 5,
        "output_cost": 15,
        "description": "OpenAI flagship. Strong reasoning + tool use; no sub-agent delegation.",
        "experimental": True,
    },
    {
        "id": "gpt-5-mini",
        "provider": "openai",
        "name": "GPT-5 Mini",
        "tier": "fast",
        "context": "400K",
        "input_cost": 0.50,
        "output_cost": 2,
        "description": "OpenAI cost-efficient model. Best for high-volume tool calls.",
        "experimental": True,
    },
    # Gemini
    {
        "id": "gemini-2.5-pro",
        "provider": "gemini",
        "name": "Gemini 2.5 Pro",
        "tier": "premium",
        "context": "2M",
        "input_cost": 1.25,
        "output_cost": 5,
        "description": "Google flagship. Best for very long context windows.",
        "experimental": True,
    },
    {
        "id": "gemini-2.5-flash",
        "provider": "gemini",
        "name": "Gemini 2.5 Flash",
        "tier": "fast",
        "context": "1M",
        "input_cost": 0.10,
        "output_cost": 0.40,
        "description": "Cheapest streaming model. Good for EDA on cost-sensitive runs.",
        "experimental": True,
    },
]


@router.get("/models")
async def get_models():
    return MODELS


@router.get("/providers")
async def get_providers():
    """List active LLM providers — frontend uses this to gate non-default models."""
    return [{"id": p} for p in list_providers()]


@router.get("/agents")
async def get_agents():
    """List all available agents with their configuration."""
    return list_all_agents()
