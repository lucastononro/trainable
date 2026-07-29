"""start-training handler — transitions an experiment to TRAINING and
freezes the hyperparams the agent intends to use.

Also the enforcement point for the user's pre-flight training controls
(project.training_config — issue #104): the agent's declared framework,
optimization metric, and trial budget are validated against the user's
configured constraints before the training window opens.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from db import async_session
from models import Experiment, ExperimentState, Project
from services.experiments import transition_state

logger = logging.getLogger(__name__)


def _normalize_metric(metric: str) -> str:
    """Normalize a metric name for comparison: 'ROC-AUC' == 'roc_auc'."""
    return metric.strip().lower().replace("-", "_").replace(" ", "_")


def _check_constraints(
    training_config: dict,
    *,
    framework: str,
    optimization_metric: str,
    max_trials: int | None,
) -> str | None:
    """Validate the agent's declared training plan against the user's
    pre-flight controls. Returns an error message, or None if compliant."""
    cfg = training_config or {}

    families = cfg.get("model_families") or []
    if families and framework.strip().lower() not in families:
        return (
            f"framework '{framework}' is not allowed by the user's training "
            f"constraints. Allowed model families: {', '.join(families)}. "
            f"Pick one of those instead."
        )

    # When the user configured a metric or a trial budget, the agent must
    # DECLARE those fields — otherwise the constraint could be bypassed by
    # simply omitting the argument.
    user_metric = cfg.get("optimization_metric")
    if user_metric:
        if not optimization_metric:
            return (
                f"optimization_metric is required: the user configured "
                f"'{user_metric}' as the metric to optimize. Re-call with "
                f"optimization_metric='{user_metric}'."
            )
        if _normalize_metric(optimization_metric) != _normalize_metric(user_metric):
            return (
                f"optimization_metric '{optimization_metric}' conflicts with the "
                f"user's configured metric '{user_metric}'. You must optimize "
                f"'{user_metric}'."
            )

    trial_cap = cfg.get("max_trials")
    if trial_cap:
        if max_trials is None:
            return (
                f"max_trials is required: the user configured a trial budget of "
                f"{trial_cap}. Re-call declaring max_trials (at most {trial_cap})."
            )
        if max_trials > int(trial_cap):
            return (
                f"max_trials={max_trials} exceeds the user's trial budget of "
                f"{trial_cap}. Re-plan the sweep with at most {trial_cap} trials."
            )

    return None


def _active_constraints(training_config: dict) -> dict:
    """The subset of the user's training config worth echoing to the agent."""
    cfg = training_config or {}
    keys = (
        "optimization_metric",
        "model_families",
        "max_trials",
        "max_wallclock_minutes",
        "max_cost_usd",
    )
    return {k: cfg[k] for k in keys if cfg.get(k)}


def create_handler(*, session_id: str = "", publish_fn=None, **_):
    async def handler(args: dict):
        eid = str(args.get("experiment_id") or "").strip()
        framework = str(args.get("framework") or "").strip()
        hyperparams = args.get("hyperparams") or {}
        optimization_metric = str(args.get("optimization_metric") or "").strip()
        raw_max_trials = args.get("max_trials")
        try:
            max_trials = int(raw_max_trials) if raw_max_trials is not None else None
        except (TypeError, ValueError):
            max_trials = None

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_start",
                {
                    "tool": "start-training",
                    "input": {
                        "experiment_id": (args.get("experiment_id") or "")[:8] + "…",
                        "framework": args.get("framework") or "(none)",
                    },
                },
                role="tool",
            )

        output_text = ""
        is_error = False
        response: dict

        def _error(text: str) -> dict:
            return {"content": [{"type": "text", "text": text}], "is_error": True}

        if not eid or not framework:
            output_text = (
                "start-training failed: experiment_id and framework are required"
            )
            is_error = True
            response = _error("experiment_id and framework are required")
        else:
            try:
                training_config: dict = {}
                # Stash framework + hyperparams on the experiment row before the
                # state transition so they're visible the moment the lineage view
                # refreshes on `experiment_state_changed`.
                async with async_session() as db:
                    exp = (
                        await db.execute(select(Experiment).where(Experiment.id == eid))
                    ).scalar_one_or_none()
                    if not exp:
                        output_text = (
                            f"start-training failed: Experiment {eid} not found"
                        )
                        is_error = True
                        response = _error(f"Experiment {eid} not found")
                    else:
                        # Enforce the user's pre-flight training controls
                        # (project.training_config) at the skill boundary.
                        if exp.project_id:
                            project = (
                                await db.execute(
                                    select(Project).where(Project.id == exp.project_id)
                                )
                            ).scalar_one_or_none()
                            if project:
                                training_config = project.training_config or {}

                        violation = _check_constraints(
                            training_config,
                            framework=framework,
                            optimization_metric=optimization_metric,
                            max_trials=max_trials,
                        )
                        if violation:
                            output_text = f"start-training rejected: {violation}"
                            is_error = True
                            response = _error(f"start-training rejected: {violation}")
                        else:
                            # Hyperparams + framework live on the eventual
                            # RegisteredModel row, but we stash a snapshot in
                            # description until then so the state-change SSE
                            # carries useful context. Keep this minimal.
                            if framework and not (exp.description or "").startswith(
                                "framework="
                            ):
                                extras = ""
                                if optimization_metric:
                                    extras += f"; metric={optimization_metric}"
                                if max_trials:
                                    extras += f"; max_trials={max_trials}"
                                exp.description = (
                                    f"framework={framework}; "
                                    f"hyperparams={json.dumps(hyperparams)[:300]}"
                                    f"{extras}"
                                )
                            await db.commit()

                if not is_error:
                    row = await transition_state(
                        experiment_id=eid,
                        new_state=ExperimentState.TRAINING.value,
                        started_at=datetime.now(timezone.utc).isoformat(),
                    )
                    summary = {
                        "experiment_id": row["id"],
                        "state": row["state"],
                        "started_at": row["started_at"],
                        "framework": framework,
                    }
                    if optimization_metric:
                        summary["optimization_metric"] = optimization_metric
                    if max_trials:
                        summary["max_trials"] = max_trials
                    constraints = _active_constraints(training_config)
                    reminder = ""
                    if constraints:
                        summary["user_constraints"] = constraints
                        reminder = (
                            "\n\nUser training constraints are active for this "
                            "project — honor them for the whole run:\n"
                            + json.dumps(constraints, indent=2)
                        )
                    output_text = (
                        f"Training started for experiment {row['id'][:8]}… "
                        f"framework={framework}"
                    )
                    response = {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Training started. Call register-model immediately "
                                    "after .fit() completes — otherwise this experiment "
                                    "will be marked abandoned.\n\n"
                                    + json.dumps(summary, indent=2)
                                    + reminder
                                ),
                            }
                        ]
                    }
            except ValueError as e:
                output_text = f"start-training failed: {e}"
                is_error = True
                response = _error(f"start-training failed: {e}")
            except Exception as e:
                logger.exception("start-training unexpected failure")
                output_text = f"start-training error: {e}"
                is_error = True
                response = _error(f"start-training error: {e}")

        if publish_fn:
            await publish_fn(
                session_id,
                "tool_end",
                {
                    "tool": "start-training",
                    "output": output_text,
                    "is_error": is_error,
                },
                role="tool",
            )
        return response

    return handler
