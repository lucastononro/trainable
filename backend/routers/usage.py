"""Usage / cost rollup endpoints."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import async_session
from models import UsageEvent

router = APIRouter()


def _day_bucket(iso_ts: str | None) -> str:
    if not iso_ts:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return datetime.fromisoformat(iso_ts).date().isoformat()
    except (ValueError, TypeError):
        return iso_ts[:10]


def _summarize(events: list[dict]) -> dict:
    """Aggregate raw events by day / agent / model / session + totals.

    Costs are split into two buckets so the UI can show them side by side:
      - llm_cost_usd      → token-based LLM calls
      - compute_cost_usd  → infrastructure (sandbox/Modal) wall-time × rate

    The legacy `cost_usd` total stays as the sum of both, for callers that
    just want one number.
    """
    by_day: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "llm_cost_usd": 0.0,
            "compute_cost_usd": 0.0,
            "sandbox_seconds": 0.0,
        }
    )
    by_agent: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "llm_cost_usd": 0.0,
            "compute_cost_usd": 0.0,
            "sandbox_seconds": 0.0,
        }
    )
    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    )
    by_session: dict[str, dict] = defaultdict(
        lambda: {
            "session_id": "",
            "cost_usd": 0.0,
            "llm_cost_usd": 0.0,
            "compute_cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "compute_seconds": 0.0,
            "llm_calls": 0,
            "compute_runs": 0,
            "agents": [],  # unique agent_types seen in this session
            "models": [],  # unique model strings seen in this session
            "first_seen": None,
            "last_seen": None,
        }
    )
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cost_usd": 0.0,
        "llm_cost_usd": 0.0,
        "compute_cost_usd": 0.0,
        "sandbox_seconds": 0.0,  # legacy alias kept for back-compat
        "compute_seconds": 0.0,
        "llm_calls": 0,
        "sandbox_runs": 0,  # legacy alias kept for back-compat
        "compute_runs": 0,
    }

    for ev in events:
        day = _day_bucket(ev.get("created_at"))
        agent = ev.get("agent_type") or "unknown"
        model = ev.get("model") or ev.get("provider") or "unknown"
        kind = ev.get("kind", "llm")
        cost = float(ev.get("cost_usd") or 0.0)
        sid = ev.get("session_id") or ""
        ts = ev.get("created_at")

        totals["cost_usd"] += cost
        by_day[day]["cost_usd"] += cost

        if kind == "llm":
            in_tok = int(ev.get("input_tokens") or 0)
            out_tok = int(ev.get("output_tokens") or 0)
            cache_r = int(ev.get("cache_read_input_tokens") or 0)
            cache_w = int(ev.get("cache_creation_input_tokens") or 0)

            totals["input_tokens"] += in_tok
            totals["output_tokens"] += out_tok
            totals["cache_read_input_tokens"] += cache_r
            totals["cache_creation_input_tokens"] += cache_w
            totals["llm_calls"] += 1
            totals["llm_cost_usd"] += cost

            by_day[day]["input_tokens"] += in_tok
            by_day[day]["output_tokens"] += out_tok
            by_day[day]["llm_cost_usd"] += cost

            by_agent[agent]["calls"] += 1
            by_agent[agent]["input_tokens"] += in_tok
            by_agent[agent]["output_tokens"] += out_tok
            by_agent[agent]["cost_usd"] += cost
            by_agent[agent]["llm_cost_usd"] += cost

            by_model[model]["calls"] += 1
            by_model[model]["input_tokens"] += in_tok
            by_model[model]["output_tokens"] += out_tok
            by_model[model]["cost_usd"] += cost
        else:
            secs = float(ev.get("sandbox_seconds") or 0.0)
            totals["sandbox_seconds"] += secs
            totals["compute_seconds"] += secs
            totals["sandbox_runs"] += 1
            totals["compute_runs"] += 1
            totals["compute_cost_usd"] += cost

            by_day[day]["sandbox_seconds"] += secs
            by_day[day]["compute_cost_usd"] += cost

            by_agent[agent]["sandbox_seconds"] += secs
            by_agent[agent]["cost_usd"] += cost
            by_agent[agent]["compute_cost_usd"] += cost

        # Session rollup — every event contributes whether llm or compute.
        if sid:
            b = by_session[sid]
            b["session_id"] = sid
            b["cost_usd"] += cost
            if kind == "llm":
                in_tok_s = int(ev.get("input_tokens") or 0)
                out_tok_s = int(ev.get("output_tokens") or 0)
                cache_r_s = int(ev.get("cache_read_input_tokens") or 0)
                b["llm_cost_usd"] += cost
                b["input_tokens"] += in_tok_s
                b["output_tokens"] += out_tok_s
                b["cache_read_input_tokens"] += cache_r_s
                b["llm_calls"] += 1
            else:
                b["compute_cost_usd"] += cost
                b["compute_seconds"] += float(ev.get("sandbox_seconds") or 0.0)
                b["compute_runs"] += 1
            if agent != "unknown" and agent not in b["agents"]:
                b["agents"].append(agent)
            if (
                model != "unknown"
                and model
                and model not in b["models"]
                and kind == "llm"
            ):
                b["models"].append(model)
            if ts and (b["first_seen"] is None or ts < b["first_seen"]):
                b["first_seen"] = ts
            if ts and (b["last_seen"] is None or ts > b["last_seen"]):
                b["last_seen"] = ts

    # Sort sessions by last activity, newest first.
    by_session_list = sorted(
        by_session.values(),
        key=lambda x: x["last_seen"] or "",
        reverse=True,
    )

    return {
        "totals": totals,
        "by_day": [{"date": d, **vals} for d, vals in sorted(by_day.items())],
        "by_agent": [{"agent": a, **vals} for a, vals in by_agent.items()],
        "by_model": [{"model": m, **vals} for m, vals in by_model.items()],
        "by_session": by_session_list,
        "events": events[-200:],
    }


async def _events_for(db: AsyncSession, where) -> list[dict]:
    q = select(UsageEvent).where(where).order_by(UsageEvent.id)
    rows = (await db.execute(q)).scalars().all()
    return [r.to_dict() for r in rows]


@router.get("/projects/{project_id}/usage")
async def project_usage(project_id: str):
    async with async_session() as db:
        events = await _events_for(db, UsageEvent.project_id == project_id)
    return _summarize(events)


@router.get("/sessions/{session_id}/usage")
async def session_usage(session_id: str):
    async with async_session() as db:
        events = await _events_for(db, UsageEvent.session_id == session_id)
    return _summarize(events)


@router.get("/usage/summary")
async def global_usage():
    """Org-wide rollup. (Will be filtered to current user once auth lands.)"""
    async with async_session() as db:
        q = select(UsageEvent).order_by(UsageEvent.id.desc()).limit(2000)
        rows = (await db.execute(q)).scalars().all()
        events = [r.to_dict() for r in rows][::-1]
    return _summarize(events)
