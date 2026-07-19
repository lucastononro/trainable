"""Pre-flight training controls (issue #104).

Covers the whole plumbing path:
- schemas.TrainingConfig validation via the projects API
- persistence on the Project row
- prompt-block rendering + wall-clock clamping in the agent runner
- enforcement at the start-training skill boundary
"""

from __future__ import annotations

import uuid

import pytest

from db import async_session
from models import Experiment, ExperimentState, Project
from models import Session as SessionModel
from services.agent.runner import (
    _apply_training_wallclock_cap,
    _format_training_constraints,
)
from services.skills.registry import load_handler

FULL_CONFIG = {
    "optimization_metric": "pr_auc",
    "model_families": ["lightgbm", "xgboost"],
    "max_trials": 20,
    "max_wallclock_minutes": 10,
    "max_cost_usd": 5.0,
}


# ---------------------------------------------------------------------------
# API: training_config round-trips through the projects routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_project_training_config_roundtrip(client, default_project_id):
    resp = await client.patch(
        f"/api/projects/{default_project_id}",
        json={"training_config": FULL_CONFIG},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["training_config"] == FULL_CONFIG

    resp = await client.get(f"/api/projects/{default_project_id}")
    assert resp.status_code == 200
    assert resp.json()["training_config"] == FULL_CONFIG


@pytest.mark.asyncio
async def test_create_project_with_training_config(client):
    resp = await client.post(
        "/api/projects",
        json={
            "name": "constrained",
            "training_config": {"max_trials": 5},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["project"]["training_config"] == {"max_trials": 5}


@pytest.mark.asyncio
async def test_project_without_training_config_defaults_empty(client):
    resp = await client.post("/api/projects", json={"name": "plain"})
    assert resp.status_code == 200
    assert resp.json()["project"]["training_config"] == {}


@pytest.mark.asyncio
async def test_training_config_validation_rejects_bad_values(
    client, default_project_id
):
    # Unknown model family
    resp = await client.patch(
        f"/api/projects/{default_project_id}",
        json={"training_config": {"model_families": ["quantum_forest"]}},
    )
    assert resp.status_code == 422

    # Zero trials
    resp = await client.patch(
        f"/api/projects/{default_project_id}",
        json={"training_config": {"max_trials": 0}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_model_families_normalized_lowercase(client, default_project_id):
    resp = await client.patch(
        f"/api/projects/{default_project_id}",
        json={"training_config": {"model_families": ["XGBoost", " LightGBM "]}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["training_config"]["model_families"] == ["xgboost", "lightgbm"]


# ---------------------------------------------------------------------------
# Runner: prompt block + wall-clock clamp
# ---------------------------------------------------------------------------


def test_format_training_constraints_empty_config_renders_nothing():
    assert _format_training_constraints({}) == ""
    assert _format_training_constraints({"optimization_metric": None}) == ""


def test_format_training_constraints_mentions_every_control():
    block = _format_training_constraints(FULL_CONFIG)
    assert "## User training constraints" in block
    assert "pr_auc" in block
    assert "lightgbm" in block and "xgboost" in block
    assert "20" in block  # trial budget
    assert "10 minutes" in block
    assert "$5" in block


def test_wallclock_cap_clamps_training_profile_timeout():
    sandbox = {"training": {"gpu": "T4", "timeout": 3600}}
    clamped = _apply_training_wallclock_cap(sandbox, {"max_wallclock_minutes": 10})
    assert clamped["training"]["timeout"] == 600
    assert clamped["training"]["gpu"] == "T4"
    # input not mutated
    assert sandbox["training"]["timeout"] == 3600


def test_wallclock_cap_noop_without_config():
    sandbox = {"training": {"timeout": 3600}}
    assert _apply_training_wallclock_cap(sandbox, {}) is sandbox


def test_wallclock_cap_never_raises_timeout():
    # Cap above the configured timeout → keep the tighter value.
    sandbox = {"training": {"timeout": 300}}
    clamped = _apply_training_wallclock_cap(sandbox, {"max_wallclock_minutes": 60})
    assert clamped["training"]["timeout"] == 300


# ---------------------------------------------------------------------------
# Skill boundary: start-training enforces the user's constraints
# ---------------------------------------------------------------------------


async def _make_experiment(training_config: dict | None) -> str:
    """Create project (+config), session, experiment. Returns experiment_id."""
    pid, sid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with async_session() as db:
        db.add(Project(id=pid, name="t", training_config=training_config or {}))
        db.add(SessionModel(id=sid, project_id=pid))
        db.add(
            Experiment(
                id=eid,
                project_id=pid,
                session_id=sid,
                name="exp",
                dataset_ref="",
                state=ExperimentState.CREATED.value,
            )
        )
        await db.commit()
    return eid


def _start_training_handler():
    return load_handler("start-training")(session_id="test-session")


async def _experiment_state(eid: str) -> str:
    async with async_session() as db:
        exp = await db.get(Experiment, eid)
        return exp.state


@pytest.mark.asyncio
async def test_start_training_rejects_disallowed_framework():
    eid = await _make_experiment({"model_families": ["lightgbm", "xgboost"]})
    handler = _start_training_handler()
    resp = await handler({"experiment_id": eid, "framework": "pytorch"})
    assert resp.get("is_error") is True
    text = resp["content"][0]["text"]
    assert "lightgbm" in text and "xgboost" in text
    # Training window must NOT have opened.
    assert await _experiment_state(eid) == ExperimentState.CREATED.value


@pytest.mark.asyncio
async def test_start_training_rejects_over_budget_trials():
    eid = await _make_experiment({"max_trials": 20})
    handler = _start_training_handler()
    resp = await handler(
        {"experiment_id": eid, "framework": "xgboost", "max_trials": 50}
    )
    assert resp.get("is_error") is True
    assert "20" in resp["content"][0]["text"]
    assert await _experiment_state(eid) == ExperimentState.CREATED.value


@pytest.mark.asyncio
async def test_start_training_rejects_conflicting_metric():
    eid = await _make_experiment({"optimization_metric": "pr_auc"})
    handler = _start_training_handler()
    resp = await handler(
        {
            "experiment_id": eid,
            "framework": "xgboost",
            "optimization_metric": "accuracy",
        }
    )
    assert resp.get("is_error") is True
    assert "pr_auc" in resp["content"][0]["text"]


@pytest.mark.asyncio
async def test_start_training_metric_match_is_case_and_separator_insensitive():
    eid = await _make_experiment({"optimization_metric": "pr_auc"})
    handler = _start_training_handler()
    resp = await handler(
        {
            "experiment_id": eid,
            "framework": "xgboost",
            "optimization_metric": "PR-AUC",
        }
    )
    assert not resp.get("is_error"), resp
    assert await _experiment_state(eid) == ExperimentState.TRAINING.value


@pytest.mark.asyncio
async def test_start_training_compliant_call_echoes_constraints():
    eid = await _make_experiment(dict(FULL_CONFIG))
    handler = _start_training_handler()
    resp = await handler(
        {
            "experiment_id": eid,
            "framework": "lightgbm",
            "hyperparams": {"n_estimators": 100},
            "optimization_metric": "pr_auc",
            "max_trials": 15,
        }
    )
    assert not resp.get("is_error"), resp
    text = resp["content"][0]["text"]
    assert await _experiment_state(eid) == ExperimentState.TRAINING.value
    assert "user_constraints" in text
    assert "pr_auc" in text
    assert '"max_trials": 15' in text  # agent's declared plan in the summary


@pytest.mark.asyncio
async def test_start_training_without_config_behaves_as_before():
    """No training_config → legacy behavior: any framework, no constraint echo."""
    eid = await _make_experiment(None)
    handler = _start_training_handler()
    resp = await handler(
        {
            "experiment_id": eid,
            "framework": "pytorch",
            "hyperparams": {"lr": 0.01},
        }
    )
    assert not resp.get("is_error"), resp
    text = resp["content"][0]["text"]
    assert "user_constraints" not in text
    assert "Training started" in text
    assert await _experiment_state(eid) == ExperimentState.TRAINING.value
