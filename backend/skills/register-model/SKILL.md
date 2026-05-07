---
name: register-model
description: Register a trained model artifact and close the training window. Marks the experiment as `trained`, copies the artifact to the project-level registry, and triggers the reproducibility snapshot.
when_to_use: IMMEDIATELY after .fit() succeeds and you've saved the artifact to the volume. This is mandatory — without it, the experiment auto-flags as abandoned and the model isn't in the registry.
version: '0.1'
kind: capability
---

# register-model

Closes the training window opened by `start-training` and persists the
model in the project-level registry so it survives session cleanup and
can be deployed.

> **Critical: every artifact carries a description.**
> Before you call this, you've drafted a 1-3 sentence description that
> says what makes this model distinct (architecture, hyperparams,
> training regime, headline metric). Generic descriptions like "best
> model" are useless to the user. The lineage canvas displays this
> alongside the metric.

## Inputs

- `experiment_id` (required): from `create-experiment` / `start-training`.
- `path` (required): volume path to the saved artifact (e.g. `/sessions/{session_id}/model.pkl`). The platform copies this to `/projects/{project_id}/models/{name}/v{N}/model.{ext}`.
- `framework` (required): `xgboost | lightgbm | sklearn | pytorch | tensorflow | huggingface | onnx | other`.
- `metrics` (required): test metrics dict — e.g. `{"accuracy": 0.91, "f1": 0.88, "val_auc": 0.93}`. Free-form keys; whatever you measured.
- `description` (REQUIRED, 1–3 sentences, user-facing): the user reads
  this when clicking the node in the lineage canvas. It must explain
  *what makes this artifact distinct*, not just describe what it is in
  the abstract.

  GOOD: "XGBoost depth=8, n_estimators=400, learning_rate=0.05, tuned via
  50-trial Optuna sweep on val ROC-AUC. Class weights balanced for the
  5.6:1 churn imbalance. Best val ROC-AUC 0.873, test 0.861."
  BAD: "trained model" / "xgboost" / "best model"

  If you can't articulate what's distinct, the artifact is probably
  redundant — consider whether you really need it.
- `hyperparams` (optional): the final hyperparams. If you supplied them at `start-training` they're already on the experiment row, but you can override here.
- `name` (optional): override the model name. Defaults to the experiment's name.

## Returns

```json
{
  "id": "<model_id>",
  "experiment_id": "<id>",
  "name": "...",
  "version": <int>,
  "artifact_uri": "/projects/.../v1/model.pkl",
  "framework": "...",
  "metrics_summary": {...}
}
```

## Side effects

- `experiment.state` → `trained`, `experiment.completed_at = now()`.
- A new `RegisteredModel` row is created.
- A reproducibility snapshot (dataset_hash + code_hash + manifest) is captured.
- The lineage canvas refreshes with the new model node connected to the experiment.

## Failure modes

- If the experiment isn't in `training` state, the lifecycle service rejects the call. Run `start-training` first.
- If the artifact path doesn't exist on the volume, the row is still created but `artifact_uri` falls back to your supplied path. Inspect the warning log to fix.

## Why descriptions matter

The user navigates the lineage canvas by reading these descriptions.
A node labeled "processed dataset" with no description is invisible
in practice — the user can't tell why it exists or how it differs
from other nodes. Treat the description as the FIRST thing a future
reader (you, in 3 weeks) will need to understand the run.
