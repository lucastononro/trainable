---
name: Tabular Training
description: Train a strong baseline + tuned model on a prepared tabular dataset.
when_to_use: Data has been prepared (parquet splits + target identified). Need a competitive baseline model with proper validation, leaderboard metrics, and a saved artifact.
version: 0.1
---

# Goal

Given the prep outputs at `/data/sessions/{session_id}/data/{train,val,test}.parquet`
(or the most recent prep session referenced in prep metadata), produce:

1. A baseline model (xgboost or lightgbm — whichever is faster on the data)
2. A tuned model (Optuna, ≤ 30 trials) — only if baseline beats a trivial benchmark
3. Saved pickle at `/data/sessions/{session_id}/model.pkl`
4. `report.md` summarizing leaderboard, hyperparams, and feature importance

# Methodology

## Baseline
- Read the prep metadata (target, feature_columns, problem_type) from
  ProcessedDatasetMeta — never re-derive.
- For classification: XGBoost or LightGBM with default params
- For regression: same, but with `objective="reg:squarederror"` / lightgbm regression
- Always train on `train.parquet`, evaluate on `val.parquet`,
  final score on `test.parquet` ONCE at the end.
- Stream metrics via `trainable.log(step, {"loss": ..., "val_auc": ...})` every
  50 trees so the live dashboard renders.

## Tuning
- Skip if baseline val score is already saturated (>0.99 AUC, etc.)
- Optuna with TPE sampler, MedianPruner, 30-trial budget
- Search space: see `scripts/sweep_xgb.py` for a sane default
- Tag each trial via the `run` argument to `trainable.log()` so the
  parallel-coordinates view in the UI works.

## Reporting
The report MUST include:
- Leaderboard: baseline vs tuned, val + test scores
- Best hyperparameters (JSON block)
- Feature importance (top 15) as bar chart
- Confusion matrix (classification) or residual plot (regression)
- A 1-paragraph "what to try next" paragraph

# Bundled scripts

- `scripts/xgboost_baseline.py` — drop-in baseline trainer
- `scripts/lightgbm_baseline.py` — same, lightgbm flavor
- `scripts/sweep_xgb.py` — Optuna sweep with a sensible default search space

Run them via execute_code (`heavy=true` is recommended for sweeps):

```python
import sys
sys.path.insert(0, "/skills/train-tabular/scripts")
import xgboost_baseline
result = xgboost_baseline.run(
    train_path="/data/sessions/.../data/train.parquet",
    val_path="/data/sessions/.../data/val.parquet",
    target="...",
    save_to="/data/sessions/.../model.pkl",
)
print(result)
```
