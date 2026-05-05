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
    """Aggregate raw events by day/agent/model + totals."""
    by_day: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "sandbox_seconds": 0.0,
        }
    )
    by_agent: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "sandbox_seconds": 0.0,
        }
    )
    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    )
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cost_usd": 0.0,
        "sandbox_seconds": 0.0,
        "llm_calls": 0,
        "sandbox_runs": 0,
    }

    for ev in events:
        day = _day_bucket(ev.get("created_at"))
        agent = ev.get("agent_type") or "unknown"
        model = ev.get("model") or ev.get("provider") or "unknown"
        kind = ev.get("kind", "llm")

        totals["cost_usd"] += float(ev.get("cost_usd") or 0.0)
        by_day[day]["cost_usd"] += float(ev.get("cost_usd") or 0.0)

        if kind == "llm":
            totals["input_tokens"] += int(ev.get("input_tokens") or 0)
            totals["output_tokens"] += int(ev.get("output_tokens") or 0)
            totals["cache_read_input_tokens"] += int(
                ev.get("cache_read_input_tokens") or 0
            )
            totals["cache_creation_input_tokens"] += int(
                ev.get("cache_creation_input_tokens") or 0
            )
            totals["llm_calls"] += 1

            by_day[day]["input_tokens"] += int(ev.get("input_tokens") or 0)
            by_day[day]["output_tokens"] += int(ev.get("output_tokens") or 0)

            by_agent[agent]["calls"] += 1
            by_agent[agent]["input_tokens"] += int(ev.get("input_tokens") or 0)
            by_agent[agent]["output_tokens"] += int(ev.get("output_tokens") or 0)
            by_agent[agent]["cost_usd"] += float(ev.get("cost_usd") or 0.0)

            by_model[model]["calls"] += 1
            by_model[model]["input_tokens"] += int(ev.get("input_tokens") or 0)
            by_model[model]["output_tokens"] += int(ev.get("output_tokens") or 0)
            by_model[model]["cost_usd"] += float(ev.get("cost_usd") or 0.0)
        else:
            totals["sandbox_seconds"] += float(ev.get("sandbox_seconds") or 0.0)
            totals["sandbox_runs"] += 1
            by_day[day]["sandbox_seconds"] += float(ev.get("sandbox_seconds") or 0.0)
            by_agent[agent]["sandbox_seconds"] += float(
                ev.get("sandbox_seconds") or 0.0
            )
            by_agent[agent]["cost_usd"] += float(ev.get("cost_usd") or 0.0)

    return {
        "totals": totals,
        "by_day": [{"date": d, **vals} for d, vals in sorted(by_day.items())],
        "by_agent": [{"agent": a, **vals} for a, vals in by_agent.items()],
        "by_model": [{"model": m, **vals} for m, vals in by_model.items()],
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
