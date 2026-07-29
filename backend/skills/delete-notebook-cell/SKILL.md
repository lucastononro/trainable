---
name: delete-notebook-cell
description: Remove a cell from a notebook by cell_id. The UI updates live. Pair with edit-notebook-cell to keep notebooks curated instead of append-only.
when_to_use: Removing a dead-end cell — a failed experiment, a duplicated paste, a scratch check that doesn't belong in the final narrative. Use read-notebook to find the cell_id first.
version: '0.1'
kind: capability
---

# delete-notebook-cell

Delete a single cell from a notebook, identified by `cell_id`. The
on-volume `.ipynb` is updated immediately and the UI reflects the removal
live.

Deleting is permanent for that cell — if the cell's outputs fed later
cells, those later cells still hold their old outputs; rerun them with
rerun-notebook-cell if the notebook should be recomputed.

## When to use
Pruning cells that shouldn't survive: failed attempts, duplicate pastes,
scratch checks. Notebooks are curated documents — append is not the only
verb.

## Inputs
- `notebook_name` (required): target notebook (e.g. "data-overview").
- `cell_id` (required): id of the cell to delete.

## Returns
```json
{ "ok": true, "notebook_name": "data-overview", "cell_id": "abc123", "remaining_cells": 4 }
```

## Failure modes
- Returns error when the notebook or the `cell_id` does not exist.
