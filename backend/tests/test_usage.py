"""Cost computation + skill discovery sanity tests."""

from __future__ import annotations

from services.skills import list_skills, load_skill
from services.usage import compute_llm_cost, compute_sandbox_cost


def test_compute_llm_cost_sonnet_basic():
    # Sonnet 4.6: $3/M input, $15/M output. 1M in + 1M out = $18.
    cost = compute_llm_cost(
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == 18.0


def test_compute_llm_cost_with_cache_read():
    # Cache reads price at 10% of input. 100k cached + 0 fresh = $0.03 (10%
    # of $0.30 for 100k @ $3/M).
    cost = compute_llm_cost(
        model="claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=100_000,
    )
    assert abs(cost - 0.03) < 1e-9


def test_compute_llm_cost_with_cache_write():
    # Cache writes price at 125% of input. 100k @ $3/M * 1.25 = $0.375
    cost = compute_llm_cost(
        model="claude-sonnet-4-6",
        cache_creation_input_tokens=100_000,
    )
    assert abs(cost - 0.375) < 1e-9


def test_compute_llm_cost_unknown_model_returns_zero():
    assert compute_llm_cost(model="my-totally-fake-model", input_tokens=1_000_000) == 0.0


def test_compute_sandbox_cost_cpu_default():
    # Unknown gpu falls back to cpu rate; 60s should be > 0
    c = compute_sandbox_cost(60.0, gpu=None)
    assert c > 0
    assert c < 0.01  # cpu is cheap


def test_compute_sandbox_cost_gpu_more_expensive():
    cpu = compute_sandbox_cost(60.0, gpu="cpu")
    gpu = compute_sandbox_cost(60.0, gpu="A100")
    assert gpu > cpu * 10


def test_skills_discovered():
    catalog = list_skills()
    slugs = {s["slug"] for s in catalog}
    # Pilots created during Tier 0.2 must show up.
    assert "eda-report" in slugs
    assert "train-tabular" in slugs


def test_load_skill_returns_body_and_files():
    skill = load_skill("eda-report")
    assert skill["slug"] == "eda-report"
    assert len(skill["body"]) > 100
    assert any(f["path"].startswith("scripts/") for f in skill["files"])
    assert skill["sandbox_root"] == "/skills/eda-report"


def test_load_skill_unknown_raises():
    import pytest

    with pytest.raises(KeyError):
        load_skill("nonexistent-skill-xyz")
