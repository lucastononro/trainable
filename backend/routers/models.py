"""Model and agent catalog endpoints."""

from fastapi import APIRouter

from services.agent.agents import list_all_agents

router = APIRouter()

MODELS = [
    {
        "id": "claude-opus-4-7",
        "name": "Claude Opus 4.7",
        "tier": "premium",
        "context": "1M",
        "input_cost": 15,
        "output_cost": 75,
        "description": "Latest flagship model. Strongest reasoning and long-horizon planning.",
    },
    {
        "id": "claude-opus-4-6",
        "name": "Claude Opus 4.6",
        "tier": "premium",
        "context": "1M",
        "input_cost": 15,
        "output_cost": 75,
        "description": "Previous-generation flagship. Great for complex analysis and multi-step reasoning.",
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "tier": "standard",
        "context": "1M",
        "input_cost": 3,
        "output_cost": 15,
        "description": "Balanced speed and intelligence. Great for everyday tasks and agentic workflows.",
    },
    {
        "id": "claude-haiku-4-5",
        "name": "Claude Haiku 4.5",
        "tier": "fast",
        "context": "200K",
        "input_cost": 0.80,
        "output_cost": 4,
        "description": "Fastest model. Ideal for quick iterations, code review, and high-volume tasks.",
    },
]


@router.get("/models")
async def get_models():
    return MODELS


@router.get("/agents")
async def get_agents():
    """List all available agents with their configuration."""
    return list_all_agents()
