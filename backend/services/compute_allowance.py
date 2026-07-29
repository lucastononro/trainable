"""Compute allowance — which GPUs/CPUs the agent may request, and how long
a single execution may run.

One resolver, two consumers: the execute-code handler (enforcement) and
the system-prompt renderer in services/agent/runner.py (presentation).
Sharing it guarantees the prompt never advertises hardware the handler
would reject.

Semantics:
  allowed = {"cpu"} ∪ {profile GPUs} ∪ (sandbox_config.allowed_gpus or ∅)
    Profile GPUs are implicitly allowed — the agent can already reach
    them via heavy=True, so denying them on the explicit arg would be
    security theater. "cpu" is always allowed (it's the floor).
  max_timeout = sandbox_config.max_timeout
    or max(profile timeouts, settings.sandbox_timeout)
    so the agent can't self-grant a longer run than the owner ever
    configured.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import settings
from schemas import CANONICAL_GPUS

# Legacy / sloppy labels seen in old project configs and the pre-canonical
# frontend dropdown. Maps lowercase alias -> canonical label.
_LABEL_ALIASES: dict[str, str] = {
    "a100": "A100-40GB",  # the old frontend 'A100' option
    "none": "cpu",
}

_CANONICAL_BY_LOWER = {g.lower(): g for g in CANONICAL_GPUS}


def normalize_gpu(label: str | None) -> str | None:
    """Case-insensitive map of a user/agent-supplied label to its
    canonical form. Returns None for unknown labels."""
    if not label:
        return None
    key = str(label).strip().lower()
    key = _LABEL_ALIASES.get(key, key).lower()
    return _CANONICAL_BY_LOWER.get(key)


@dataclass(frozen=True)
class ComputeAllowance:
    allowed_gpus: list[str]  # canonical labels incl. "cpu", cost-ascending
    max_timeout: int  # seconds
    explicit: bool  # owner set allowed_gpus (vs profile-implied only)

    def permits(self, label: str) -> bool:
        return label in self.allowed_gpus


def resolve_compute_allowance(sandbox_config: dict | None) -> ComputeAllowance:
    cfg = sandbox_config or {}
    profiles = [cfg.get("default") or {}, cfg.get("training") or {}]

    allowed: set[str] = {"cpu"}
    for profile in profiles:
        normalized = normalize_gpu(profile.get("gpu"))
        if normalized:
            allowed.add(normalized)

    configured = cfg.get("allowed_gpus") or None
    if configured:
        for label in configured:
            normalized = normalize_gpu(label)
            if normalized:
                allowed.add(normalized)

    max_timeout = cfg.get("max_timeout") or max(
        [p.get("timeout") or 0 for p in profiles] + [settings.sandbox_timeout]
    )

    order = {g: i for i, g in enumerate(CANONICAL_GPUS)}
    return ComputeAllowance(
        allowed_gpus=sorted(allowed, key=lambda g: order.get(g, 99)),
        max_timeout=int(max_timeout),
        explicit=bool(configured),
    )


def clamp_timeout(requested, allowance: ComputeAllowance) -> int | None:
    """Clamp an agent-requested timeout into [10, allowance.max_timeout].
    Returns None for garbage input (caller falls back to the profile)."""
    try:
        return max(10, min(int(requested), allowance.max_timeout))
    except (TypeError, ValueError):
        return None
