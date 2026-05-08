---
name: register-model
description: Register a trained model artifact and close the training window. Marks the experiment as `trained`, copies the artifact to the project-level registry, and triggers the reproducibility snapshot.
when_to_use: IMMEDIATELY after .fit() succeeds and you've saved the artifact to the volume. This is mandatory — without it, the experiment auto-flags as abandoned and the model isn't in the registry.
version: '0.2'
kind: capability
---

# register-model

Closes the training window opened by `start-training` and persists the
model in the project-level registry so it survives session cleanup and
can be deployed.

> **Two non-negotiable inputs.**
> 1. **`description`** — what makes this model distinct (architecture,
>    hyperparams, regime, headline metric). Generic = useless.
> 2. **`training_dataset_id`** — the **processed** dataset's id (from
>    `register-dataset` or `list-project-datasets`). This is the
>    PROVENANCE LINK that lets the lineage canvas read
>    Raw → Processed → Model. Pass a raw dataset id and the call is
>    rejected.
>
> If you held out validation/test splits as separate dataset versions,
> reference them too via `validation_dataset_id` / `test_dataset_id`
> and supply per-split metrics in `split_metrics`. The model card in
> the UI will show **train / val / test** rows side-by-side, each
> carrying its own dataset name + metrics — so a future reader can
> see at a glance how the model performed on each cut of data.

## Why these references matter

The model artifact is just a pickle on disk. What gives it meaning is
the data it saw. Without explicit `training_dataset_id`, the lineage
canvas falls back to the experiment's input list, which often contains
**both** the raw upload AND the processed file — and the resulting
graph shows a misleading direct line `raw → model` that hides the
preparation work. The references on the model row fix this:

- The canvas draws **one edge per split** from the cited dataset
  version into the model.
- The model card displays per-split metrics next to each dataset name.
- Anyone auditing the run can answer "what data did this train on, and
  what did it score on each split?" by clicking the model node.

## Inputs

- `experiment_id` (required): from `create-experiment` / `start-training`.
- `path` (required): volume path to the saved artifact (e.g.
  `/sessions/{session_id}/model.pkl`). The platform copies this to
  `/projects/{project_id}/models/{name}/v{N}/model.{ext}`.
- `framework` (required): `xgboost | lightgbm | sklearn | pytorch | tensorflow | huggingface | onnx | other`.
- `metrics` (required): the headline metric dict (test by default) —
  e.g. `{"accuracy": 0.91, "f1": 0.88}`.
- `description` (REQUIRED, 1–3 sentences, user-facing): explain *what
  makes this artifact distinct*, not just describe what it is.
  - GOOD: "XGBoost depth=8, n_estimators=400, learning_rate=0.05, tuned via
    50-trial Optuna sweep on val ROC-AUC. Class weights balanced for the
    5.6:1 churn imbalance."
  - BAD: "trained model" / "xgboost" / "best model"
- `training_dataset_id` (REQUIRED, integer): the `dataset_version_id`
  returned by `register-dataset` for the **processed** training data
  this model fit on. Pass a raw dataset id and you get an error
  pointing you back at register-dataset. If you don't know the id,
  call `list-project-datasets` first.
- `validation_dataset_id` (optional, integer): set when you held out a
  validation split as its own DatasetVersion row.
- `test_dataset_id` (optional, integer): set when you held out a test
  split as its own DatasetVersion row.
- `split_metrics` (optional, object): per-split metrics keyed by role.
  - Example:
    ```json
    {
      "train": {"accuracy": 0.99, "loss": 0.04},
      "val":   {"accuracy": 0.94, "loss": 0.18},
      "test":  {"accuracy": 0.91, "loss": 0.22}
    }
    ```
  - The UI renders each row next to the dataset it came from.
- `hyperparams` (optional): the final hyperparams. If you supplied them
  at `start-training` they're already on the experiment row.
- `name` (optional): override the model name. Defaults to the
  experiment's name.

## Worked example (one dataset version, internal split)

You have one processed dataset that contains the whole train/val/test
inside it (e.g. you used `train_test_split` in pandas at fit time):

```text
register-model(
  experiment_id="exp-...",
  path="/sessions/<sid>/model.pkl",
  framework="xgboost",
  description="XGBoost depth=4 n_estimators=200, default reg, 70/15/15 random split.",
  training_dataset_id=12,                # the processed parquet
  metrics={"accuracy": 0.91},            # headline = test
  split_metrics={
    "train": {"accuracy": 0.99},
    "val":   {"accuracy": 0.94},
    "test":  {"accuracy": 0.91},
  },
)
```

## Worked example (separate processed datasets per split)

You ran `register-dataset` three times — once per split — so train, val,
and test each have their own DatasetVersion row:

```text
register-model(
  experiment_id="exp-...",
  path="...",
  framework="xgboost",
  description="...",
  training_dataset_id=12,
  validation_dataset_id=13,
  test_dataset_id=14,
  metrics={"accuracy": 0.91},
  split_metrics={"train": {...}, "val": {...}, "test": {...}},
)
```

## Returns

```json
{
  "id": "<model_id>",
  "experiment_id": "<id>",
  "name": "...",
  "version": <int>,
  "artifact_uri": "/projects/.../v1/model.pkl",
  "framework": "...",
  "metrics_summary": {...},
  "dataset_refs": {
    "train": {"dataset_id": 12, "metrics": {...}},
    "val":   {"dataset_id": 13, "metrics": {...}},
    "test":  {"dataset_id": 14, "metrics": {...}}
  }
}
```

## Side effects

- `experiment.state` → `trained`, `experiment.completed_at = now()`.
- A new `RegisteredModel` row is created with the dataset references
  pinned to it.
- A reproducibility snapshot (dataset_hash + code_hash + manifest) is
  captured.
- The lineage canvas refreshes — the model node now shows one edge per
  cited split into its training data.

## Failure modes

- `training_dataset_id` missing → call rejected with a hint to run
  `register-dataset` first.
- `training_dataset_id` points at a `kind='raw'` row → call rejected.
  Raw uploads aren't valid training inputs in the canvas; they have to
  pass through `register-dataset` to declare a processed version first.
- Any cited dataset id belongs to a different project → call rejected.
- If the experiment isn't in `training` state, the lifecycle service
  rejects the call. Run `start-training` first.
- If the artifact path doesn't exist on the volume, the row is still
  created but `artifact_uri` falls back to your supplied path.
