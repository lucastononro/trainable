"""Structured EDA findings tests (issue #111)."""

import pytest

from services.agent.agents import get_agent_skills
from services.skills import get_skill, load_handler

SLUG = "report-eda-findings"


class _PublishRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event_type, data, role=None, **kwargs):
        self.events.append(
            {"session_id": session_id, "type": event_type, "data": data, "role": role}
        )


def _make_handler(recorder: _PublishRecorder):
    create_handler = load_handler(SLUG)
    return create_handler(
        session_id="sess-eda-1",
        publish_fn=recorder,
        parent_agent_type="eda",
        parent_agent_id="eda-1",
        parent_parent_agent_id="root",
        current_depth=1,
        stage="eda",
    )


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_skill_is_discoverable_with_schema():
    skill = get_skill(SLUG)
    assert skill.has_handler
    assert skill.schema["required"] == ["findings"]
    item_schema = skill.schema["properties"]["findings"]["items"]
    assert set(item_schema["required"]) == {"finding_type", "summary", "recommendation"}


def test_eda_agent_declares_the_skill():
    assert SLUG in get_agent_skills("eda")


def test_other_agents_untouched():
    """Default behavior elsewhere is unchanged: only eda gains the skill."""
    for agent in (
        "chat",
        "orchestrator",
        "data_prep",
        "feature_eng",
        "trainer",
        "reviewer",
    ):
        assert SLUG not in get_agent_skills(agent), agent


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_publishes_normalized_findings():
    recorder = _PublishRecorder()
    handler = _make_handler(recorder)
    result = await handler(
        {
            "findings": [
                {
                    "finding_type": "leakage",
                    "columns": ["customer_id"],
                    "severity": "critical",
                    "summary": "customer_id perfectly predicts the target.",
                    "recommendation": "Drop `customer_id` before modeling.",
                },
                {
                    "finding_type": "class_imbalance",
                    "columns": ["churned"],
                    "summary": "Positive class is 4.2% of rows.",
                    "recommendation": "Use stratified splits and class weights.",
                },
            ]
        }
    )
    assert not result.get("is_error")
    assert "Published 2 structured EDA finding(s)" in result["content"][0]["text"]

    assert len(recorder.events) == 1
    ev = recorder.events[0]
    assert ev["type"] == "eda_findings"
    assert ev["role"] == "system"
    data = ev["data"]
    assert data["count"] == 2
    assert data["stage"] == "eda"
    f0, f1 = data["findings"]
    assert f0["finding_type"] == "leakage"
    assert f0["severity"] == "critical"
    assert f0["columns"] == ["customer_id"]
    # severity defaulted when omitted
    assert f1["severity"] == "warning"


@pytest.mark.asyncio
async def test_handler_coerces_malformed_fields():
    recorder = _PublishRecorder()
    handler = _make_handler(recorder)
    result = await handler(
        {
            "findings": [
                {
                    "finding_type": "not-a-real-type",
                    "columns": "single_col",  # not a list
                    "severity": "apocalyptic",
                    "summary": "  Something odd.  ",
                    "recommendation": "Handle it.",
                },
                {"finding_type": "outliers", "summary": "", "recommendation": "x"},
            ]
        }
    )
    assert not result.get("is_error")
    assert "(1 invalid item(s) dropped)" in result["content"][0]["text"]
    data = recorder.events[0]["data"]
    assert data["count"] == 1
    f = data["findings"][0]
    assert f["finding_type"] == "other"  # unknown type coerced
    assert f["severity"] == "warning"  # unknown severity coerced
    assert f["columns"] == ["single_col"]  # scalar wrapped
    assert f["summary"] == "Something odd."  # stripped


@pytest.mark.asyncio
async def test_handler_rejects_empty_batch():
    recorder = _PublishRecorder()
    handler = _make_handler(recorder)
    for bad in ({}, {"findings": []}, {"findings": "nope"}):
        result = await handler(bad)
        assert result["is_error"]
    # all-invalid items are also an error, and NOTHING is published
    result = await handler({"findings": [{"summary": "", "recommendation": ""}]})
    assert result["is_error"]
    assert recorder.events == []


@pytest.mark.asyncio
async def test_handler_caps_batch_size():
    recorder = _PublishRecorder()
    handler = _make_handler(recorder)
    findings = [
        {"finding_type": "other", "summary": f"s{i}", "recommendation": f"r{i}"}
        for i in range(60)
    ]
    result = await handler({"findings": findings})
    assert not result.get("is_error")
    assert recorder.events[0]["data"]["count"] == 50
    assert (
        "(10 item(s) omitted over the 50-finding cap)" in result["content"][0]["text"]
    )
