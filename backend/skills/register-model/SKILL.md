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

## Inputs

- `experiment_id` (required): from `create-experiment` / `start-training`.
- `path` (required): volume path to the saved artifact (e.g. `/sessions/{session_id}/model.pkl`). The platform copies this to `/projects/{project_id}/models/{name}/v{N}/model.{ext}`.
- `framework` (required): `xgboost | lightgbm | sklearn | pytorch | tensorflow | huggingface | onnx | other`.
- `metrics` (required): test metrics dict — e.g. `{"accuracy": 0.91, "f1": 0.88, "val_auc": 0.93}`. Free-form keys; whatever you measured.
- `description` (required, 1-2 sentences): what makes this model unique. Examples: `"XGBoost depth=8, n_estimators=300, tuned via 30-trial Optuna sweep"` / `"LightGBM with early stopping at iter 142, scale_pos_weight=3.5"`. The user reads this in the model node's metadata panel.
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
