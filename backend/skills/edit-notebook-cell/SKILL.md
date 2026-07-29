---
name: edit-notebook-cell
description: Replace the source of an existing notebook cell in place (outputs preserved). Notebooks are no longer append-only — fix a cell instead of appending a corrected copy.
when_to_use: Changing the code or markdown of a cell that already exists — fixing a bug in an earlier cell, tightening a query, rewriting a markdown summary. Use read-notebook to find the cell_id first.
version: '0.1'
kind: capability
---

# edit-notebook-cell

Replace a cell's `source` in place, identified by `cell_id`. The cell's
outputs are preserved (same behavior as the frontend's editor), so the
notebook keeps its rendered results until you rerun the cell.

Editing a code cell does NOT re-execute it — pair with rerun-notebook-cell
when the new source should run. The UI updates live via the same
notebook-structure event the other notebook skills emit.

## When to use
Fixing or refining an existing cell instead of appending a corrected
duplicate. Get `cell_id` values from read-notebook or from the result of
append-notebook-cell / run-notebook-cell.

## Inputs
- `notebook_name` (required): target notebook (e.g. "data-overview").
- `cell_id` (required): id of the cell to edit.
- `source` (required): full new cell source (Python for code cells,
  markdown for markdown cells).
- `cell_type` (optional): `"code"` or `"markdown"` — converts the cell
  type when different from the current one.

## Returns
```json
{ "ok": true, "notebook_name": "data-overview", "cell_id": "abc123", "source_len": 210 }
```

## Failure modes
- Returns error when the notebook or the `cell_id` does not exist.
- Returns error when `source` is not a string.
