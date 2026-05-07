---
name: register-dataset
description: Register a processed dataset (the output of a prep step) under an experiment so it shows up in the lineage graph and can be referenced by register-model.
when_to_use: Immediately after writing prep outputs (train/val/test parquet, scaled features, etc.) to the volume. Before starting training on this data.
version: '0.1'
kind: capability
---

# register-dataset

Tells the platform: "this file on the volume is the canonical input for
this experiment's training step." Without this call, the dataset will
not appear in the lineage graph and downstream tools cannot reference it.

## Inputs

- `experiment_id` (required): from `create-experiment`.
- `path` (required): volume path of the dataset file or directory you wrote (e.g. `/sessions/{session_id}/data/train.parquet`).
- `name` (required): human-readable label, e.g. `"train splits, scaled"`.
- `description` (required, 1-2 sentences): what was done to produce this version. Examples: `"One-hot encoded categoricals, dropped 3 leakage columns, 80/10/10 split"`. The user reads this when inspecting the lineage graph.
- `content_hash` (required): the file's SHA-256 hex string. If you wrote multiple files, hash a deterministic concatenation. Use the `hashlib` module from inside `execute-code`:
  ```python
  import hashlib, pathlib
  data = pathlib.Path("/sessions/.../train.parquet").read_bytes()
  print(hashlib.sha256(data).hexdigest())
  ```
- `size_bytes` (optional but encouraged): file size for the metadata panel.
- `role` (optional, default `"input"`): `"input"` (this dataset feeds the experiment's model) or `"output"` (the experiment produced this dataset, e.g. a feature store derivative).
- `parent_dataset_id` (optional but strongly preferred): the `dataset_version_id` of the source you derived from (the raw upload, or a previous processed version). Without this the lineage graph won't connect raw → processed.
- `metadata` (optional): structured dict — `{columns, target_column, feature_columns, train_rows, val_rows, test_rows, quality_stats}`. Goes into the dataset's metadata panel so the user can inspect it without re-querying.

## Returns

```json
{
  "id": <int>,
  "kind": "processed",
  "name": "...",
  "description": "...",
  "hash": "<sha256>"
}
```

Save the `id` if you'll use it as `parent_dataset_id` for a follow-on dataset.

## Notes

- Re-registering the same content (same hash) returns the existing row — safe to call idempotently.
- The platform never sniffs file extensions to identify datasets. Your declaration is the source of truth.
