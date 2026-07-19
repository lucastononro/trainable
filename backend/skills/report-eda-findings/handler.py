"""report-eda-findings skill — structured EDA findings for canvas cards.

Normalizes the agent's findings batch and publishes a single `eda_findings`
event (SSE + persisted as a system Message, so reloads restore the cards).
The prose report.md flow is untouched — this is an additive channel.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MAX_FINDINGS = 50
_MAX_COLUMNS = 40
_MAX_TEXT_CHARS = 2000

VALID_FINDING_TYPES = {
    "leakage",
    "class_imbalance",
    "high_cardinality",
    "multicollinearity",
    "missing_values",
    "outliers",
    "duplicates",
    "skewed_target",
    "id_column",
    "constant_column",
    "datetime_leakage",
    "other",
}

VALID_SEVERITIES = {"info", "warning", "critical"}


def _clip(value, limit: int = _MAX_TEXT_CHARS) -> str:
    text = str(value or "").strip()
    return text[:limit]


def normalize_finding(raw: dict) -> dict | None:
    """Coerce one raw finding into the canonical wire shape, or None if it
    lacks the actionable minimum (summary + recommendation)."""
    if not isinstance(raw, dict):
        return None
    summary = _clip(raw.get("summary"))
    recommendation = _clip(raw.get("recommendation"))
    if not summary or not recommendation:
        return None

    finding_type = str(raw.get("finding_type") or "").strip()
    if finding_type not in VALID_FINDING_TYPES:
        finding_type = "other"

    severity = str(raw.get("severity") or "").strip()
    if severity not in VALID_SEVERITIES:
        severity = "warning"

    columns_raw = raw.get("columns") or []
    if not isinstance(columns_raw, list):
        columns_raw = [columns_raw]
    columns = [_clip(c, 200) for c in columns_raw[:_MAX_COLUMNS] if _clip(c, 200)]

    return {
        "finding_type": finding_type,
        "columns": columns,
        "severity": severity,
        "summary": summary,
        "recommendation": recommendation,
    }


def create_handler(
    session_id: str,
    publish_fn,
    parent_agent_type: str,
    parent_agent_id: str = "root",
    parent_parent_agent_id: str | None = None,
    current_depth: int = 0,
    stage: str = "",
    **kwargs,
):
    agent_meta = {
        "agent_id": parent_agent_id,
        "agent_type": parent_agent_type,
        "parent_agent_id": parent_parent_agent_id,
        "depth": current_depth,
    }

    async def handler(args: dict):
        raw = args.get("findings")
        if not isinstance(raw, list) or not raw:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "findings must be a non-empty array of finding objects",
                    }
                ],
                "is_error": True,
            }

        findings = []
        dropped = 0
        for item in raw[:_MAX_FINDINGS]:
            norm = normalize_finding(item)
            if norm is None:
                dropped += 1
                continue
            findings.append(norm)
        dropped += max(0, len(raw) - _MAX_FINDINGS)

        if not findings:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "No valid findings: every item needs a non-empty "
                            "`summary` and `recommendation`."
                        ),
                    }
                ],
                "is_error": True,
            }

        await publish_fn(
            session_id,
            "eda_findings",
            {
                "content": f"{len(findings)} structured EDA finding(s) published",
                "findings": findings,
                "count": len(findings),
                "stage": stage or parent_agent_type,
            },
            role="system",
            agent_meta=agent_meta,
        )

        note = f" ({dropped} invalid item(s) dropped)" if dropped else ""
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Published {len(findings)} structured EDA finding(s) to "
                        f"the studio canvas{note}. The user can apply each "
                        "recommendation in prep with one click."
                    ),
                }
            ]
        }

    return handler
