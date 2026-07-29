---
name: read-notebook
description: 'Read a session notebook as compact markdown. Behaviour depends on args:'
when_to_use: Inspect existing notebooks in this session.
version: '0.1'
kind: capability
---

# read-notebook

Read a session notebook as compact markdown. Behaviour depends on args:

- `read_notebook()` with no args → lists every notebook in this session
  (names + cell counts + paths).
- `read_notebook(notebook_name="xxx")` → returns the cells + outputs of
  the named notebook.

Use this before building on prior work — each notebook may contain
analyses or models the user has iterated on. You can also find a
`cell_id` to pass to `append_notebook_cell(after_cell_id=...)`.

Outputs are truncated per cell; binary outputs (PNG, HTML) are
summarised rather than returned verbatim.

## When to use
Inspect existing notebooks in this session.
