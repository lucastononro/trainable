---
name: list-project-datasets
description: List every DatasetVersion (raw + processed) in the current project so you can pick a parent_dataset_id when registering processed datasets.
when_to_use: BEFORE calling register-dataset on a processed file, so you know which raw dataset to link as the parent. Also useful for inspecting what other agents have already produced in the same session.
version: '0.1'
kind: capability
---

# list-project-datasets

Returns every DatasetVersion in the project the current session belongs to,
with kind (raw|processed), name, description, hash, and parent_id chain. The
agent uses this to populate `parent_dataset_id` correctly when calling
register-dataset.

## Inputs

None — the skill resolves project_id from the current session.

## Returns

```json
{
  "datasets": [
    {
      "id": 1,
      "kind": "raw",
      "name": "iris.csv",
      "description": "user upload",
      "hash": "abc123…",
      "parent_id": null,
      "created_at": "..."
    },
    ...
  ]
}
```

## Why this matters

Without this call, you don't know which raw upload feeds your prep step.
The lineage canvas then shows the raw upload disconnected from the
experiment. ALWAYS call list-project-datasets before register-dataset so
you can supply parent_dataset_id correctly.
