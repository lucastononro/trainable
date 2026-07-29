---
name: run-notebook-cell
description: Append a code cell to a named notebook AND execute it in the live kernel,
when_to_use: Add a cell and execute it in the persistent session kernel.
version: '0.1'
kind: capability
---

# run-notebook-cell

Append a code cell to a named notebook AND execute it in the live kernel,
then return its outputs. Unlike `execute_code` (one-shot sandbox, no
notebook touch), this cell lands in the user's interactive notebook
under `/data/sessions/{session_id}/notebooks/{notebook_name}.ipynb`
where they can edit/re-run it later.

The kernel is persistent and shared across ALL notebooks in this session:
variables set in one notebook are visible in the next. Use that to
split an analysis into multiple themed notebooks (e.g. "data-overview",
"baseline-model") without losing state.

Outputs (stdout, stderr, last value, display_data, errors) stream to the
user's UI in real time. Large outputs are summarised in the response.

Returns JSON with `notebook_name`, `cell_id`, `exec_count`, `duration_ms`,
`had_error`, and compact `outputs`.

## When to use
Add a cell and execute it in the persistent session kernel.
