---
name: append-notebook-cell
description: Append a cell to a named notebook under
when_to_use: Add a cell to the user's interactive notebook without executing it.
version: '0.1'
kind: capability
---

# append-notebook-cell

Append a cell to a named notebook under
`/data/sessions/{session_id}/notebooks/{notebook_name}.ipynb`.
Creates the notebook if it doesn't exist yet.

Use this to drop cells into the user's interactive notebook WITHOUT
executing — great for markdown narrative, TODO cells, or code you want
the user to review before running. Use `run_notebook_cell` when you want
the cell to execute immediately.

Multiple notebooks per session are supported — organise by analysis
theme (e.g. "data-overview", "target-distribution", "baseline-model").
Notebook names are slugged automatically (alphanumerics + `-_` only).

## When to use
Add a cell to the user's interactive notebook without executing it.
