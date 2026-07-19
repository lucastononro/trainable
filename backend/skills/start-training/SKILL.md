---
name: start-training
description: Mark an experiment as `training`. Call IMMEDIATELY before .fit() runs so the platform knows training started; call register-model when it finishes.
when_to_use: At the moment you're about to invoke .fit() / .train() / equivalent. Do not call earlier (defer until you've finalized hyperparams).
version: '0.1'
kind: capability
---

# start-training

Opens the training window for an experiment. The platform records:
- `experiment.state = 'training'`
- `experiment.started_at = now()`
- `experiment.hyperparams = <your declared hyperparams>` (frozen for reproducibility)

If you call `start-training` and your turn ends without calling
`register-model`, the experiment is auto-flagged **abandoned** by the
post-stage cleanup hook. The user then sees a warning chip in the
sidebar — which is the signal that something went wrong.

## Inputs

- `experiment_id` (required): from `create-experiment`.
- `framework` (required): one of `xgboost | lightgbm | sklearn | pytorch | tensorflow | huggingface | other`.
- `hyperparams` (optional but encouraged): the dict you'll pass to `.fit()`. Saved on the experiment row so the lineage view can show "this model was trained with these hyperparams" without parsing the snapshot manifest.
- `optimization_metric` (optional): the metric your tuning loop optimizes (e.g. `roc_auc`, `pr_auc`, `f1`, `rmse`).
- `max_trials` (optional): the number of hyperparameter-search trials you plan to run.

## User training constraints

Projects can carry pre-flight training controls set by the user in Project
Settings (optimization metric, allowed model families, trial budget,
wall-clock/cost cap). When set, this skill enforces them:

- `framework` outside the allowed model families → **rejected**.
- `optimization_metric` that conflicts with the user's metric → **rejected**.
- `max_trials` above the user's trial budget → **rejected**.

A successful call echoes the active constraints back in `user_constraints` —
honor them for the whole run. If no constraints are configured, the call
behaves exactly as before.

## Returns

```json
{
  "experiment_id": "<id>",
  "state": "training",
  "started_at": "<ISO timestamp>"
}
```

## Failure modes

- Calling `start-training` on a `trained` experiment is allowed (re-train), but uncommon — typically you should `create-experiment` for a new attempt.
- Calling on a non-existent experiment_id returns an error.
