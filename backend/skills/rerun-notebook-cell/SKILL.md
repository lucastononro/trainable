---
name: rerun-notebook-cell
description: Re-execute an existing notebook cell (by cell_id) against the session's persistent kernel — outputs are cleared and streamed back fresh; variables from earlier cells stay in scope.
when_to_use: Re-running a cell after edit-notebook-cell changed its source, or recomputing a cell after upstream data changed — without appending a duplicate cell. Only works on code cells.
version: '0.1'
kind: capability
---

# rerun-notebook-cell

Re-execute an existing code cell, identified by `cell_id`, against the
session's long-lived kernel — the same kernel run-notebook-cell uses, so
variables, imports, and fitted objects from earlier cells remain in
scope. The cell's previous outputs are cleared and the new outputs
stream back into the notebook live.

This is the missing half of edit-notebook-cell: edit changes the source,
rerun makes it real. Neither appends a new cell — the notebook stays
curated.

Note that `import` from `src/` works inside a kernel cell — after
write-file(`src/loaders.py`, ...), a rerun of a cell containing
`from loaders import load` picks the module up with no kernel restart.

## When to use
Recomputing an existing cell: after editing its source, after upstream
data changed, or to refresh stale outputs.

## Inputs
- `notebook_name` (required): target notebook (e.g. "data-overview").
- `cell_id` (required): id of the code cell to re-execute.
- `timeout_seconds` (optional, default 300, max 1800): max wall-clock time.

## Returns
Same shape as run-notebook-cell:
```json
{ "ok": true, "notebook_name": "data-overview", "cell_id": "abc123",
  "exec_count": 7, "duration_ms": 412, "had_error": false, "outputs": "..." }
```

## Failure modes
- Returns error when the notebook or the `cell_id` does not exist.
- Returns error when the cell is a markdown cell (nothing to execute).
- Returns error when execution exceeds `timeout_seconds`.
