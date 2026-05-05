---
name: execute-code
description: Execute Python code in an isolated Modal sandbox.
when_to_use: Run any Python in an isolated Modal sandbox — EDA, modeling, validation.
version: '0.1'
kind: capability
---

# execute-code

Execute Python code in an isolated Modal sandbox.
Pre-installed: pandas, numpy, matplotlib, seaborn, scikit-learn,
xgboost, lightgbm, pyarrow, openpyxl, duckdb, imbalanced-learn,
optuna, category_encoders, pandera, shap, statsmodels,
torch, torchvision, torchaudio, tensorflow.
Dataset files at /data/datasets/{experiment_id}/.
Save outputs to /data/sessions/{session_id}/{stage}/.
Use os.makedirs(path, exist_ok=True) before saving.
Print all results to stdout.
Each execution has a 10-minute timeout by default.

Set heavy=true for GPU-intensive workloads (model training,
hyperparameter tuning, large-scale data processing). This uses the
project's training sandbox profile which may have a GPU attached
and a longer timeout.

## When to use
Run any Python in an isolated Modal sandbox — EDA, modeling, validation.
