"""Cost tracking — record token & sandbox usage as `usage_events` rows.

The single source of truth for pricing is `routers/models.py::MODELS`. Costs
are computed at insert time so rollups don't need to know the table.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from db import async_session
from models import Experiment, Session as SessionModel, UsageEvent
from observability import get_tracer, record_usage_attributes
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)


# Per-million-token prices in USD. Mirrors backend/routers/models.py and
# extends to non-Claude providers we plan to support behind the LLM factory.
# Cache reads price at 10% of input; cache writes at 125% — Anthropic's
# published ephemeral cache pricing. OpenAI and Gemini handle caching
# differently (implicit / context-cached), so the `cache_read_*` columns may
# be zero for those providers; we treat them as plain input tokens.
_PRICING: dict[str, dict[str, float]] = {
    # Claude
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
    # OpenAI (illustrative — kept here so the LLM factory can resolve costs
    # without a separate config)
    "gpt-5": {"input": 5.0, "output": 15.0},
    "gpt-5-mini": {"input": 0.50, "output": 2.0},
    # Gemini
    "gemini-2.5-pro": {"input": 1.25, "output": 5.0},
    "gemini-2.5-flash": {"input": 0.10, "output": 0.40},
    # LiteLLM-routed backends (model-id encodes the backend)
    "groq/llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "mistral/mistral-large-latest": {"input": 2.0, "output": 6.0},
    "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
}

# Sandbox compute pricing (approximate, USD/second). Real Modal pricing is
# per-CPU-second and varies by GPU; this is a coarse upper bound for
# user-facing cost visibility.
_SANDBOX_PRICING = {
    "cpu": 0.0000168,        # ~$0.06/hr equivalent
    "T4": 0.000162,          # ~$0.59/hr
    "L4": 0.000222,          # ~$0.80/hr
    "A10G": 0.000305,        # ~$1.10/hr
    "A100": 0.001100,        # ~$3.96/hr
    "A100-80GB": 0.001533,   # ~$5.52/hr
    "H100": 0.002317,        # ~$8.34/hr
}


def _per_token(price_per_million: float) -> float:
    return price_per_million / 1_000_000.0


def compute_llm_cost(
    *,
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> float:
    """Compute cost for a single LLM call. Unknown models return 0.0."""
    if not model:
        return 0.0
    p = _PRICING.get(model)
    if not p:
        # Strip vendor prefixes like "anthropic:" or "openai:".
        bare = model.split(":")[-1]
        p = _PRICING.get(bare, {})
    if not p:
        return 0.0
    in_rate = _per_token(p["input"])
    out_rate = _per_token(p["output"])
    cost = input_tokens * in_rate + output_tokens * out_rate
    cost += cache_read_input_tokens * in_rate * 0.10
    cost += cache_creation_input_tokens * in_rate * 1.25
    return cost


def compute_sandbox_cost(seconds: float, gpu: str | None) -> float:
    rate = _SANDBOX_PRICING.get(gpu or "cpu", _SANDBOX_PRICING["cpu"])
    return max(0.0, seconds) * rate


async def _resolve_project_id(session_id: str) -> str | None:
    try:
        async with async_session() as db:
            row = await db.execute(
                select(Experiment.project_id)
                .join(SessionModel, SessionModel.experiment_id == Experiment.id)
                .where(SessionModel.id == session_id)
            )
            return row.scalar_one_or_none()
    except Exception as e:
        logger.debug("Could not resolve project for session %s: %s", session_id, e)
        return None


async def record_llm_usage(
    *,
    session_id: str,
    agent_type: str | None,
    agent_id: str | None,
    provider: str,
    model: str | None,
    usage: dict[str, Any] | None,
    is_error: bool = False,
    extra: dict | None = None,
) -> dict | None:
    """Persist + broadcast a single LLM call's token usage.

    `usage` is the raw Anthropic-shaped dict returned by claude-agent-sdk's
    ResultMessage.usage (input_tokens, output_tokens, cache_*_input_tokens).
    Other providers should normalize to this shape before calling.
    """
    if not usage:
        return None

    tracer = get_tracer()
    with tracer.start_as_current_span("llm.usage") as _usage_span:
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        cache_r = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_w = int(usage.get("cache_creation_input_tokens", 0) or 0)

        cost = compute_llm_cost(
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_input_tokens=cache_r,
            cache_creation_input_tokens=cache_w,
        )

        record_usage_attributes(
            _usage_span,
            provider=provider,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read=cache_r,
            cost_usd=cost,
        )
        try:
            _usage_span.set_attribute("session.id", session_id)
            if agent_type:
                _usage_span.set_attribute("agent.type", agent_type)
            if is_error:
                _usage_span.set_attribute("error", True)
        except Exception:
            pass

    project_id = await _resolve_project_id(session_id)

    row_dict: dict | None = None
    try:
        async with async_session() as db:
            ev = UsageEvent(
                session_id=session_id,
                project_id=project_id,
                kind="llm",
                agent_type=agent_type,
                agent_id=agent_id,
                provider=provider,
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_input_tokens=cache_r,
                cache_creation_input_tokens=cache_w,
                cost_usd=cost,
                is_error=is_error,
                extra=extra or {},
            )
            db.add(ev)
            await db.commit()
            await db.refresh(ev)
            row_dict = ev.to_dict()
    except Exception as e:
        logger.error("Failed to record LLM usage: %s", e)
        return None

    try:
        # Compute cache-hit ratio inline so the frontend doesn't have to.
        total_input = in_tok + cache_r + cache_w
        cache_hit_pct = (cache_r / total_input * 100.0) if total_input else 0.0
        payload = dict(row_dict)
        payload["cache_hit_pct"] = round(cache_hit_pct, 1)
        await broadcaster.publish(
            session_id, {"type": "usage_event", "data": payload}
        )
    except Exception as e:
        logger.debug("Usage broadcast failed: %s", e)

    return row_dict


async def record_sandbox_usage(
    *,
    session_id: str,
    agent_type: str | None,
    agent_id: str | None,
    seconds: float,
    gpu: str | None = None,
    is_error: bool = False,
    extra: dict | None = None,
) -> dict | None:
    """Persist + broadcast a single sandbox execution's compute time."""
    cost = compute_sandbox_cost(seconds, gpu)

    tracer = get_tracer()
    with tracer.start_as_current_span("sandbox.usage") as _span:
        try:
            _span.set_attribute("sandbox.seconds", float(seconds))
            _span.set_attribute("sandbox.cost_usd", float(cost))
            _span.set_attribute("session.id", session_id)
            if gpu:
                _span.set_attribute("sandbox.gpu", gpu)
            if agent_type:
                _span.set_attribute("agent.type", agent_type)
            if is_error:
                _span.set_attribute("error", True)
        except Exception:
            pass

    project_id = await _resolve_project_id(session_id)

    row_dict: dict | None = None
    try:
        async with async_session() as db:
            ev = UsageEvent(
                session_id=session_id,
                project_id=project_id,
                kind="sandbox",
                agent_type=agent_type,
                agent_id=agent_id,
                provider="modal",
                model=None,
                sandbox_seconds=seconds,
                gpu_type=gpu,
                cost_usd=cost,
                is_error=is_error,
                extra=extra or {},
            )
            db.add(ev)
            await db.commit()
            await db.refresh(ev)
            row_dict = ev.to_dict()
    except Exception as e:
        logger.error("Failed to record sandbox usage: %s", e)
        return None

    try:
        await broadcaster.publish(
            session_id, {"type": "usage_event", "data": row_dict}
        )
    except Exception as e:
        logger.debug("Usage broadcast failed: %s", e)

    return row_dict
