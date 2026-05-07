---
name: EDA Report
description: Run a thorough exploratory data analysis and produce a written report.
when_to_use: A new dataset has been uploaded and the user needs a profile, schema, target/leakage assessment, and a written report before any modeling.
version: 0.1
---

# Goal

Produce a complete EDA artifact set for the dataset under
`/data/projects/{project_id}/datasets/`:

1. A markdown narrative at `/data/sessions/{session_id}/report.md`
2. Charts under `/data/sessions/{session_id}/figures/`
3. A live notebook at `/data/sessions/{session_id}/notebooks/data-overview.ipynb`
   (created via `run_notebook_cell`, not `execute_code`)

# Methodology — checklist

For every dataset, walk this list. Skip a step ONLY when it is impossible
(e.g. no numeric columns ⇒ no correlation matrix), and call that out in the
report.

## Dataset overview
- shape (rows, cols), dtypes, memory footprint
- missing values: count + percentage per column; missingness pattern (MCAR vs structured)
- duplicate rows: exact count + sample

## Numeric columns (per column)
- mean, median, std, min, max
- skewness (`pandas.Series.skew()`), kurtosis
- outlier count via IQR rule (1.5× IQR fences)
- histogram

## Categorical columns (per column)
- cardinality (n_unique)
- top values + frequencies
- rare categories (<1% frequency) — list them
- bar chart of top-10

## Target column
- Identify the likely target. State the problem type:
  - Classification (binary / multiclass) — plot class balance, report ratio
  - Regression — plot distribution, report skewness, suggest log-transform if |skew| > 1

## Feature ↔ target signal
- Numeric features: Pearson correlation with target
- Categorical features: chi-squared p-value with target (classification),
  ANOVA F-stat (regression)
- Rank features by signal strength; report the top 10

## Leakage / pitfalls
- Columns that perfectly predict the target → likely leakage
- ID-like columns (high cardinality, monotonically increasing) → exclude
- Date/time columns: check for forward-looking leakage (e.g. timestamp after target event)

## Multicollinearity
- Correlation matrix heatmap
- Flag every numeric pair with |r| > 0.9

# Deliverable shape

`report.md` MUST include these sections, in this order:

```
# EDA Report — {dataset_name}

## 1. Dataset at a glance
- Rows / cols / dtypes / memory
- Missing values summary

## 2. Target
- Column, problem type, distribution

## 3. Feature signal ranking
- Top 10 features ranked by correlation/chi-sq with target

## 4. Notable patterns
- Skew, outliers, multicollinearity

## 5. Leakage / data quality flags
- Anything that should be dropped or treated specially

## 6. Recommendations for prep
- Bullet list: imputation, encoding, scaling, splits, sampling
```

Keep the report ≤ 800 lines. Long stat tables → reference the JSON file in
`figures/` instead of inlining.

# Bundled helpers

This skill ships these scripts (mounted read-only at `/skills/eda-report/`):

- `scripts/profile.py` — one-shot dataset profiler. Imports as
  `from sys.path.insert(0, '/skills/eda-report/scripts'); import profile`
  and call `profile.run(path, target=None)` to emit the standard set of
  charts + a JSON profile to a directory you choose.

# Tool choice

For EDA work prefer **`run_notebook_cell`** over `execute_code`. The
notebook is the user-facing deliverable; one-shot scripts are throwaway.
