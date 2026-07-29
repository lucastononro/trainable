---
name: register-raw-dataset
description: Register a raw user-uploaded data file as a DatasetVersion(kind='raw') so it shows up as the starting node in the lineage canvas. Idempotent — re-registering the same bytes returns the existing row.
when_to_use: Right after `list-project-datasets` if you find data files that aren't yet in the catalog. Always do this BEFORE running prep, otherwise the lineage graph has no starting point and Raw → Processed → Model becomes Processed → Model with no source.
version: '0.1'
kind: capability
---

# register-raw-dataset

Force-register a file on the volume as a raw dataset. The
`POST /api/experiments` upload route already does this for files
uploaded through the UI — but agents that find unregistered files on
the volume (uploaded via S3 sync, copied from another session, etc.)
need to declare them so they appear as the **starting node** in the
lineage canvas.

> **Always run this BEFORE prep when the raw isn't already in the
> catalog.** A processed dataset registered without a parent raw
> shows up as an orphan node — the canvas can't render the
> `Raw → Processed → Model` chain because it has no Raw to anchor on.
> This is the whole point of the lineage view; don't skip it.

## Inputs

- `path` (required): volume path, e.g.
  `/projects/{project_id}/datasets/titanic.csv`. The file must exist
  on the volume.
- `project_id` OR `experiment_id` (required, exactly one): scope. If
  you pass `experiment_id` we resolve project from the experiment row.
- `name` (optional): label for the canvas; defaults to the file's
  basename.
- `description` (optional but strongly recommended): where the data
  came from. Examples:
  - `"Kaggle Titanic competition train.csv, 891 rows × 12 cols."`
  - `"S3 export from prod analytics on 2026-05-08; user_events with PII stripped."`
  - `"User uploaded via UI on 2026-05-07."`

## Returns

```json
{
  "id": <int>,
  "project_id": "...",
  "kind": "raw",
  "name": "...",
  "hash": "...",
  "path": "...",
  "size_bytes": <int>
}
```

## Idempotency

The skill hashes the file's bytes and dedupes — calling it twice with
the same content returns the existing row. Safe to call at the top
of every run as a "make sure raw is in the catalog" pre-flight.

## Standard pattern at session start

```
list-project-datasets()        # what's already known?
# If the file you need is missing OR has kind != 'raw':
register-raw-dataset(path=..., project_id=..., description=...)
# Now you have a raw dataset_version_id to pass as
# parent_dataset_id in register-dataset for processed splits.
```

Skip this step at your peril: the lineage canvas needs a raw node
for every model to read as `Raw → Processed → Model` instead of an
orphan processed → model line.
