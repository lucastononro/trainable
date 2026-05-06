"""Cost tracking — record token & sandbox usage as `usage_events` rows.

Pricing is split across two YAML files (single source of truth, located
next to the code that owns each domain):
  - backend/services/llm/models.yml   LLM catalog: per-model cost + display metadata
  - backend/services/sandbox.yml      Per-(provider, gpu) compute rates
Costs are computed at insert time so rollups don't need to know the table.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from db import async_session
from models import Experiment, Session as SessionModel, UsageEvent
from observability import get_tracer, record_usage_attributes
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing catalog loader
# ---------------------------------------------------------------------------
#
# Two YAML files, each colocated with the code that owns it:
#   - services/llm/models.yml   model catalog (cost + display) + cache fallback
#   - services/sandbox.yml      compute pricing per (provider, gpu)
#
# Each can be edited independently and the loader picks them up on next
# call (after reload_pricing() in dev, on backend restart in prod). The
# YAML doubles as the catalog the model-picker UI consumes via /api/models,
# so any non-Claude entries (OpenAI, Gemini, LiteLLM-routed backends) live
# in models.yml — no per-provider hardcoded dict here.

_SERVICES_DIR = Path(__file__).parent
_LLM_MODELS_FILE = _SERVICES_DIR / "llm" / "models.yml"
_SANDBOX_FILE = _SERVICES_DIR / "sandbox.yml"

# Cache pricing fallback used when models.yml is missing the `cache:`
# block. Matches Anthropic ephemeral-cache pricing.
_DEFAULT_CACHE_READ_MULT = 0.10
_DEFAULT_CACHE_CREATION_MULT = 1.25


def _read_yaml(path: Path, kind: str) -> dict:
    """Read a YAML file as a mapping. Returns {} on missing/malformed."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("[cost] %s not found at %s — falling back to empty", kind, path)
        return {}
    except yaml.YAMLError as e:
        logger.error("[cost] %s parse error: %s — falling back to empty", kind, e)
        return {}
    if not isinstance(data, dict):
        logger.error(
            "[cost] %s root must be a mapping, got %s", kind, type(data).__name__
        )
        return {}
    return data


@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, Any]:
    """Read services/llm/models.yml + services/sandbox.yml exactly once.
    Call `reload_pricing()` to drop the cache.

    Returned shape (kept stable across the file split for back-compat):
        {
            "llm":     {<model_id>: {input, output, cache_read?, cache_creation?,
                                     name?, provider?, tier?, context?,
                                     description?, experimental?}},
            "compute": {<provider>: {<gpu>: usd_per_second}},
            "cache":   {read_multiplier: float, creation_multiplier: float},
        }
    """
    llm_doc = _read_yaml(_LLM_MODELS_FILE, "services/llm/models.yml")
    sandbox_doc = _read_yaml(_SANDBOX_FILE, "services/sandbox.yml")

    llm = llm_doc.get("models") or {}
    cache = llm_doc.get("cache") or {}

    if not isinstance(llm, dict):
        logger.error(
            "[cost] services/llm/models.yml: 'models' must be a mapping — got %s",
            type(llm).__name__,
        )
        llm = {}

    # services/sandbox.yml is just `<provider>: {<gpu>: rate}` at the top level.
    compute: dict = {}
    for prov, rates in sandbox_doc.items():
        if isinstance(rates, dict):
            compute[prov] = rates
        else:
            logger.error(
                "[cost] services/sandbox.yml: provider %r must be a mapping — got %s",
                prov,
                type(rates).__name__,
            )

    return {"llm": llm, "compute": compute, "cache": cache}


def reload_pricing() -> None:
    """Drop the lru_cache so the next call re-reads the LLM + sandbox YAMLs.

    Useful after editing either file in dev (no backend restart needed).
    """
    _load_catalog.cache_clear()


def get_llm_catalog() -> dict[str, dict]:
    """Public accessor for the LLM model catalog (services/llm/models.yml `models`).

    Returns a snake_case dict keyed on model id, with each entry carrying
    both the pricing fields (input/output/cache_*) AND the display
    metadata (name/provider/tier/context/description/experimental) used
    by the model-picker UI.

    Lives here because the loader + cache are here. The /api/models
    endpoint in routers/models.py projects this to the response shape
    the frontend expects.
    """
    return dict(_load_catalog().get("llm") or {})


def _per_token(price_per_million: float) -> float:
    return price_per_million / 1_000_000.0


def _resolve_llm_pricing(model: str) -> dict | None:
    """Resolve a model id to its pricing entry. Three-stage lookup:

    1. Exact match.
    2. Strip vendor prefix ("anthropic:claude-opus-4-7" → "claude-opus-4-7").
    3. Longest-prefix match — handles dated variants like
       "claude-haiku-4-5-20251001" → "claude-haiku-4-5".
    """
    llm = _load_catalog()["llm"]
    p = llm.get(model)
    if p:
        return p

    bare = model.split(":")[-1]
    p = llm.get(bare)
    if p:
        return p

    candidates = [(key, llm[key]) for key in llm if model.startswith(key)]
    if candidates:
        candidates.sort(key=lambda kv: len(kv[0]), reverse=True)
        return candidates[0][1]

    return None


def compute_llm_cost(
    *,
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> float:
    """Compute cost for a single LLM call. Unknown models return 0.0.

    Per-model `cache_read` / `cache_creation` keys (USD/M) override the
    global cache.read_multiplier / cache.creation_multiplier in
    services/llm/models.yml. The multipliers are applied to the model's
    `input` rate when no explicit override is set.
    """
    if not model:
        return 0.0

    p = _resolve_llm_pricing(model)
    if not p:
        return 0.0

    cache_cfg = _load_catalog().get("cache") or {}
    read_mult = float(cache_cfg.get("read_multiplier", _DEFAULT_CACHE_READ_MULT))
    creation_mult = float(
        cache_cfg.get("creation_multiplier", _DEFAULT_CACHE_CREATION_MULT)
    )

    in_rate = _per_token(float(p.get("input", 0) or 0))
    out_rate = _per_token(float(p.get("output", 0) or 0))

    # Per-model cache overrides take precedence over the multiplier path.
    cache_read_rate = (
        _per_token(float(p["cache_read"])) if "cache_read" in p else in_rate * read_mult
    )
    cache_creation_rate = (
        _per_token(float(p["cache_creation"]))
        if "cache_creation" in p
        else in_rate * creation_mult
    )

    cost = input_tokens * in_rate + output_tokens * out_rate
    cost += cache_read_input_tokens * cache_read_rate
    cost += cache_creation_input_tokens * cache_creation_rate
    return cost


_DEFAULT_COMPUTE_PROVIDER = "modal"


def _resolve_compute_rate(provider: str | None, gpu: str | None) -> float:
    """Look up a USD/second rate for (provider, gpu).

    Resolution order, in priority:
      1. <provider>.<gpu>           ← preferred (services/sandbox.yml entry)
      2. <provider>.cpu             ← gpu missing/unknown for that provider
      3. 0.0                        ← provider absent from services/sandbox.yml; cost=0

    `provider` defaults to "modal" since that's the only sandbox runner
    today. Pass an explicit provider once we add others (RunPod, Together,
    AWS Bedrock, etc.).
    """
    compute = _load_catalog().get("compute") or {}
    p = (provider or _DEFAULT_COMPUTE_PROVIDER).lower()
    gpu_key = gpu or "cpu"

    nested = compute.get(p)
    if isinstance(nested, dict):
        rate = nested.get(gpu_key)
        if rate is not None:
            return float(rate)
        rate = nested.get("cpu")
        if rate is not None:
            return float(rate)

    return 0.0


def compute_sandbox_cost(
    seconds: float,
    gpu: str | None,
    provider: str | None = None,
) -> float:
    """USD/second × wall-time. Unknown gpu strings fall through to `cpu` for
    that provider; unknown providers fall through to legacy flat keys."""
    rate = _resolve_compute_rate(provider, gpu)
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
    broadcast: bool = True,
) -> dict | None:
    """Persist (and optionally broadcast) a single LLM call's token usage.

    `usage` is the raw Anthropic-shaped dict returned by claude-agent-sdk's
    ResultMessage.usage (input_tokens, output_tokens, cache_*_input_tokens).
    Other providers should normalize to this shape before calling.

    Pass `broadcast=False` when the caller is already emitting per-turn
    `usage_event` SSE messages on its own (the runner does this for live
    badge updates) — otherwise the frontend would double-count at end of
    run when both partials AND the canonical aggregate land.
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

    if broadcast:
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
        await broadcaster.publish(session_id, {"type": "usage_event", "data": row_dict})
    except Exception as e:
        logger.debug("Usage broadcast failed: %s", e)

    return row_dict
