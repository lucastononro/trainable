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

> **Critical: every artifact carries a description and a parent.**
> Before you call this, you've called `list-project-datasets` (so you
> know the parent_dataset_id) and you've drafted a 1-3 sentence
> description that captures what's distinct. If either is missing,
> stop and produce them — don't ship a placeholder.

## Workflow

1. Call `list-project-datasets` to discover what raw uploads + processed
   datasets already exist in this project.
2. Pick the appropriate `parent_dataset_id`:
   - If you're processing a single raw upload, link to its id.
   - If you're deriving from another processed dataset (rare), link to that.
   - If genuinely producing a fresh dataset (e.g. synthetic data with no
     prior source), pass `parent_dataset_id=null` explicitly.
3. Call `register-dataset` with the parent set.

## Inputs

- `experiment_id` (required): from `create-experiment`.
- `path` (required): volume path of the dataset file or directory you wrote (e.g. `/sessions/{session_id}/data/train.parquet`).
- `name` (required): human-readable label, e.g. `"train splits, scaled"`.
- `description` (REQUIRED, 1–3 sentences, user-facing): the user reads
  this when clicking the node in the lineage canvas. It must explain
  *what makes this artifact distinct*, not just describe what it is in
  the abstract.

  GOOD: "Stratified 70/15/15 split on `churn`, one-hot encoded the 7
  categorical columns, dropped `customerID` (leakage), median-imputed
  `MonthlyCharges` nulls. Final shape 7032×31."
  BAD: "processed dataset" / "the data" / "training data v1"

  If you can't articulate what's distinct, the artifact is probably
  redundant — consider whether you really need it.
- `content_hash` (required): the file's SHA-256 hex string. If you wrote multiple files, hash a deterministic concatenation. Use the `hashlib` module from inside `execute-code`:
  ```python
  import hashlib, pathlib
  data = pathlib.Path("/sessions/.../train.parquet").read_bytes()
  print(hashlib.sha256(data).hexdigest())
  ```
- `size_bytes` (optional but encouraged): file size for the metadata panel.
- `role` (optional, default `"input"`): `"input"` (this dataset feeds the experiment's model) or `"output"` (the experiment produced this dataset, e.g. a feature store derivative).
- `parent_dataset_id` (REQUIRED unless this is your project's first dataset): the `dataset_version_id` of the source you derived from. Call **`list-project-datasets`** first to find the right raw upload to link. Without this, the lineage canvas shows your processed dataset as an orphan node and downstream tools can't trace it back to the raw upload.
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

## Why descriptions matter

The user navigates the lineage canvas by reading these descriptions.
A node labeled "processed dataset" with no description is invisible
in practice — the user can't tell why it exists or how it differs
from other nodes. Treat the description as the FIRST thing a future
reader (you, in 3 weeks) will need to understand the run.
