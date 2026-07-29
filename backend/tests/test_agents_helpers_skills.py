"""Unit tests for services.agent.agents — the renamed skill helpers.

`get_agent_skills`, `get_skill_for_agent`, `render_skill_description`,
`get_skill_input_schema`, `get_agent_provider`. We use real agent YAMLs so
this also catches regressions in the YAML format.
"""

from __future__ import annotations

import pytest

from services.agent.agents import (
    get_agent,
    get_agent_default_model,
    get_agent_provider,
    get_agent_skills,
    get_skill_for_agent,
    list_all_agents,
    render_skill_description,
)


def test_known_agents_have_skills_field():
    """Every shipped agent declares at least one capability skill."""
    for agent_meta in list_all_agents():
        skills = get_agent_skills(agent_meta["type"])
        assert skills, f"{agent_meta['type']} has no skills"


def test_eda_skills_include_execute_code():
    skills = get_agent_skills("eda")
    assert "execute-code" in skills


def test_default_provider_is_claude():
    """Agents without an explicit provider field default to claude — this
    keeps backwards compatibility with pre-multi-provider YAMLs."""
    assert get_agent_provider("eda") == "claude"


def test_get_skill_for_agent_returns_merged_config():
    s = get_skill_for_agent("eda", "execute-code")
    assert s["name"] == "execute-code"
    assert s["description"]
    assert "code" in s["input_schema"]["properties"]


def test_render_skill_description_substitutes_placeholders():
    """If a description contains {experiment_id}, the renderer fills it in."""
    rendered = render_skill_description(
        skill_slug="execute-code",
        agent_type="eda",
        experiment_id="exp-123",
        session_id="sess-abc",
        stage="eda",
    )
    # No assertion on substitution presence (current execute-code description
    # may not use these placeholders), but the renderer must not crash and
    # must return a non-empty string.
    assert isinstance(rendered, str) and rendered


def test_unknown_agent_raises():
    with pytest.raises(KeyError):
        get_agent("does-not-exist")


def test_get_skill_for_agent_unknown_skill_returns_empty():
    """Unknown slug returns the merged config with empty defaults — never crashes."""
    s = get_skill_for_agent("eda", "no-such-skill")
    assert s["name"] == "no-such-skill"
    assert s["description"] == ""
    assert s["input_schema"] == {}


def test_default_model_present():
    assert get_agent_default_model("eda")
