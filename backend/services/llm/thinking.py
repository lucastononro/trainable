"""Provider-neutral reasoning ("thinking") level translation.

Trainable exposes a single abstract knob — `off / low / medium / high` — in
the UI for any model that supports extended reasoning. This module turns
that level into the provider-native config dict at call time.

Why centralized:
  - services/llm/models.yml says *what* a model supports (which levels
    appear in the picker, which is the default). This file owns *how* we
    translate the chosen level into Anthropic / OpenAI / Gemini knobs.
  - When a provider ships a new knob shape, change it here once.

Provider-specific quirks the translator handles:
  - OpenAI GPT-5: reasoning_effort = minimal|low|medium|high. Our "off"
    maps to "minimal".
  - OpenAI GPT-5.5+: reasoning_effort = low|medium|high|xhigh — there is
    no minimal/off. The YAML levels list omits "off" for these models;
    if "off" sneaks through anyway we coerce to "low".
  - Gemini 2.5: thinking_config.thinking_budget (int tokens). budget=0
    disables, -1 = dynamic. 2.5 Pro CANNOT be disabled — YAML enforces.
  - Gemini 3.x: thinking_config.thinking_level = minimal|low|medium|high
    (DIFFERENT parameter name from 2.5). Our "off" maps to "minimal".

References:
  - Anthropic extended thinking
    https://docs.claude.com/en/docs/build-with-claude/extended-thinking
  - OpenAI reasoning effort
    https://platform.openai.com/docs/guides/reasoning
  - Gemini thinking
    https://ai.google.dev/gemini-api/docs/thinking
"""

from __future__ import annotations

from typing import Literal

ThinkingLevel = Literal["off", "low", "medium", "high"]

VALID_LEVELS: tuple[ThinkingLevel, ...] = ("off", "low", "medium", "high")

# Anthropic budget_tokens per level. Numbers chosen to roughly match what
# OpenAI / Gemini call low/medium/high — small enough to keep streaming
# responsive at "low", generous enough at "high" for hard reasoning.
_ANTHROPIC_BUDGET = {
    "low": 4_000,
    "medium": 16_000,
    "high": 64_000,
}

# Gemini thinking_budget (tokens). 0 disables thinking entirely; -1 lets
# the model decide. We map abstract levels to concrete budgets so cost is
# predictable.
_GEMINI_BUDGET = {
    "off": 0,
    "low": 2_048,
    "medium": 8_192,
    "high": 24_576,
}


def normalize_level(
    level: str | None, *, default: ThinkingLevel = "off"
) -> ThinkingLevel:
    """Coerce arbitrary user input to a valid level. Falls back to `default`."""
    if not level:
        return default
    lower = str(level).strip().lower()
    if lower in VALID_LEVELS:
        return lower  # type: ignore[return-value]
    return default


def _is_gpt_5_5_plus(model_id: str) -> bool:
    """OpenAI 5.5 and later don't accept "minimal"/"none" — minimum is low.

    Real OpenAI ids are dated snapshots like `gpt-5.5-2026-04-23` or
    `gpt-5.5-pro-2026-04-23`, so check both dot- and dash-separated minor
    versions. Currently flags 5.5 and 5.6+; broaden when later families
    confirm the same effort floor.
    """
    m = (model_id or "").lower()
    prefixes = (
        "gpt-5.5",
        "gpt-5-5",
        "gpt-5.6",
        "gpt-5-6",
    )
    return m.startswith(prefixes)


def _is_gemini_3(model_id: str) -> bool:
    """Gemini 3.x uses thinking_level (not thinking_budget)."""
    m = (model_id or "").lower()
    return m.startswith("gemini-3")


def to_provider_config(
    provider: str, level: str | None, *, model_id: str | None = None
) -> dict:
    """Translate `(provider, level, model_id)` → kwargs to merge into the call.

    Returns an empty dict when the provider doesn't support thinking, or the
    caller asked for "off" on a provider where "off" is the no-config state.
    Caller is expected to `**spread` the result into their request kwargs.

    `model_id` matters for the OpenAI 5.5+ family (no "minimal" exists) and
    the Gemini 3.x family (uses `thinking_level`, not `thinking_budget`).
    Pass it whenever you have it; falls back to safe defaults otherwise.
    """
    lvl = normalize_level(level)
    p = (provider or "").lower()
    mid = model_id or ""

    if p in ("claude", "anthropic"):
        if lvl == "off":
            return {}
        return {
            "thinking": {
                "type": "enabled",
                "budget_tokens": _ANTHROPIC_BUDGET[lvl],
            }
        }

    if p == "openai":
        if _is_gpt_5_5_plus(mid):
            # No minimal/off on 5.5+. Coerce "off" up to "low" so we don't
            # send an invalid value; the YAML's levels list should already
            # prevent this in normal flow.
            effort = "low" if lvl == "off" else lvl
            return {"reasoning_effort": effort}
        # GPT-5 / o-series: "minimal" is the floor.
        return {"reasoning_effort": "minimal" if lvl == "off" else lvl}

    if p in ("gemini", "google"):
        if _is_gemini_3(mid):
            # Gemini 3.x uses thinking_level (minimal|low|medium|high).
            tl = "minimal" if lvl == "off" else lvl
            return {"thinking_config": {"thinking_level": tl}}
        # Gemini 2.5: thinking_budget. 2.5 Pro can't disable — but enforcing
        # that lives in the YAML levels list, not here.
        return {"thinking_config": {"thinking_budget": _GEMINI_BUDGET[lvl]}}

    # Unknown provider — caller can decide to ignore.
    return {}


def anthropic_budget_tokens(level: str | None) -> int | None:
    """Convenience for the Claude path: returns budget tokens or None for off."""
    lvl = normalize_level(level)
    if lvl == "off":
        return None
    return _ANTHROPIC_BUDGET[lvl]
