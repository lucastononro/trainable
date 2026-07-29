---
name: report-eda-findings
description: >
  Publish EDA findings as structured items (finding type + affected columns +
  recommendation) so the studio renders them as actionable cards with an
  "Apply in prep" button — in addition to the prose report.md.
when_to_use: >
  At the end of an EDA pass, right after writing report.md. One call with the
  full findings list; call again later only if new findings emerge.
version: '0.1'
kind: capability
---

# report-eda-findings

Structured companion to the prose EDA report (issue #111). Each finding
becomes a canvas card the user can act on with one click — the "Apply in
prep" button pre-fills the orchestrator prompt with your `recommendation`,
so write it as a concrete, self-contained data_prep instruction.

## Finding shape

- `finding_type` — one of `leakage`, `class_imbalance`, `high_cardinality`,
  `multicollinearity`, `missing_values`, `outliers`, `duplicates`,
  `skewed_target`, `id_column`, `constant_column`, `datetime_leakage`,
  `other`.
- `columns` — the affected column names (empty for dataset-wide findings).
- `severity` — `info` | `warning` | `critical` (default `warning`).
- `summary` — one or two sentences: what you observed, with numbers.
- `recommendation` — the concrete prep action, phrased as an instruction
  data_prep can execute verbatim (e.g. "Drop `customer_id` before modeling —
  it's a unique ID that perfectly memorizes the target.").

## Rules

- Report the SAME findings your report.md narrates — this is the structured
  view of them, not a different analysis.
- One call with the whole batch; up to 50 findings.
- Every finding needs an actionable `recommendation` — if there is nothing
  to do about it, it belongs in report.md prose, not here.
