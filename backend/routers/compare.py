"""Experiment / session comparison endpoint.

Aggregates metrics + prep summaries across N sessions so the frontend can
overlay charts and diff feature lists without making N round-trips.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from db import async_session
from models import (
    Experiment,
    Metric,
    ProcessedDatasetMeta,
    Session as SessionModel,
    UsageEvent,
)

router = APIRouter()


@router.get("/compare")
async def compare(sessions: str = Query(..., description="Comma-separated session ids")):
    ids = [s.strip() for s in sessions.split(",") if s.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="no session ids provided")
    if len(ids) > 8:
        raise HTTPException(status_code=400, detail="compare up to 8 sessions at once")

    out: dict = {"sessions": [], "metrics": {}, "feature_overlap": [], "totals": {}}

    async with async_session() as db:
        # Session + experiment metadata
        sess_rows = (
            await db.execute(
                select(SessionModel, Experiment)
                .join(Experiment, SessionModel.experiment_id == Experiment.id)
                .where(SessionModel.id.in_(ids))
            )
        ).all()
        sess_map = {s.id: (s, e) for (s, e) in sess_rows}

        # Maintain user-supplied order for charts
        for sid in ids:
            if sid not in sess_map:
                out["sessions"].append({"id": sid, "missing": True})
                continue
            s, e = sess_map[sid]
            out["sessions"].append({
                "id": s.id,
                "experiment_id": e.id,
                "experiment_name": e.name,
                "state": s.state,
                "model": s.model,
                "created_at": s.created_at,
                "missing": False,
            })

        # Metrics: bucket by name, return per-step series for each session
        metrics_rows = (
            await db.execute(
                select(Metric)
                .where(Metric.session_id.in_(ids))
                .order_by(Metric.session_id, Metric.step)
            )
        ).scalars().all()
        per_metric: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for m in metrics_rows:
            per_metric[m.name][m.session_id].append({"step": m.step, "value": m.value, "stage": m.stage})
        out["metrics"] = {
            name: [
                {"session_id": sid, "points": pts}
                for sid, pts in by_session.items()
            ]
            for name, by_session in per_metric.items()
        }

        # Prep summaries → feature overlap
        prep_rows = (
            await db.execute(
                select(ProcessedDatasetMeta).where(
                    ProcessedDatasetMeta.session_id.in_(ids)
                )
            )
        ).scalars().all()
        prep_map = {p.session_id: p for p in prep_rows}
        if prep_map:
            common: set[str] | None = None
            per_session_features: dict[str, list[str]] = {}
            for sid, p in prep_map.items():
                feats = set(p.feature_columns or [])
                per_session_features[sid] = sorted(feats)
                common = feats if common is None else common & feats
            out["feature_overlap"] = {
                "common": sorted(common or set()),
                "per_session": per_session_features,
            }

        # Cost totals
        usage_rows = (
            await db.execute(
                select(UsageEvent).where(UsageEvent.session_id.in_(ids))
            )
        ).scalars().all()
        totals: dict[str, dict] = {sid: {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "sandbox_seconds": 0.0} for sid in ids}
        for ev in usage_rows:
            t = totals[ev.session_id]
            t["cost_usd"] += float(ev.cost_usd or 0.0)
            t["input_tokens"] += int(ev.input_tokens or 0)
            t["output_tokens"] += int(ev.output_tokens or 0)
            t["sandbox_seconds"] += float(ev.sandbox_seconds or 0.0)
        out["totals"] = totals

    return out
