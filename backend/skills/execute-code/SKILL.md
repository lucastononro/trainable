---
name: execute-code
description: Execute Python code in an isolated Modal sandbox.
when_to_use: Run any Python in an isolated Modal sandbox — EDA, modeling, validation.
version: '0.1'
kind: capability
---

# execute-code

Execute Python code in an isolated Modal sandbox.
For one-shot, throwaway Python — "what's the dtype of column X", "print
the head", a quick plot. Every call is auto-saved to `scripts/` as the
audit log. For anything you'd want to call again, author a file with
write-file and execute it with run-file; change existing files with
edit-file. Don't use execute-code to `.write_text()` modules into place.
Pre-installed: pandas, numpy, matplotlib, seaborn, scikit-learn,
xgboost, lightgbm, pyarrow, openpyxl, duckdb, imbalanced-learn,
optuna, category_encoders, pandera, shap, statsmodels,
torch, torchvision, torchaudio, tensorflow.
Dataset files at /data/datasets/{experiment_id}/.
Save outputs to /data/sessions/{session_id}/{stage}/.
Use os.makedirs(path, exist_ok=True) before saving.
Print all results to stdout.
Each execution has a 10-minute timeout by default.

Choosing compute — three levers, in precedence order:

- `gpu` (optional): explicitly pick hardware for this call from the
  allowed list shown in your system prompt's "Compute environment"
  section (e.g. `gpu="cpu"`, `gpu="L4"`). Overrides `heavy`. Values
  outside the allowance return an error naming the allowed set.
- `heavy=true`: fall back to the project's training sandbox profile
  (GPU + extended timeout) for GPU-intensive workloads (model training,
  hyperparameter tuning, large-scale data processing).
- `timeout` (optional): per-call timeout in seconds, clamped to the
  project's max. Use it instead of splitting work when a single fit
  slightly exceeds the profile default.

Prefer the cheapest hardware that fits the job.

## When to use
Run any Python in an isolated Modal sandbox — EDA, modeling, validation.
