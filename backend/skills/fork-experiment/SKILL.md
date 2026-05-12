---
name: fork-experiment
description: Fork an existing experiment to spawn a sibling that inherits the same input datasets — typically used to try a different model architecture or hyperparam set on the same data prep.
when_to_use: When you want to run a NEW model on the SAME data that another experiment already has registered. Forking copies the dataset linkages so you don't have to re-run prep or repeat list-project-datasets + register-dataset.
version: '0.1'
kind: capability
---

# fork-experiment

Creates a child experiment under the same session as the parent. The
new experiment inherits the parent's `role='input'` dataset links so
you can skip prep + register-dataset and jump straight to start-training.

> **Critical: every artifact carries a description.**
> The forked experiment gets a fresh `hypothesis` (1-3 sentences explaining
> what's different from the parent). Don't reuse the parent's hypothesis
> verbatim — say what you're testing in this fork.

## Inputs

- `parent_experiment_id` (required): the experiment to fork from. Must exist.
- `name` (required): a label for the new experiment. Convention: prefix with
  the parent's name and add the variation, e.g. `"xgb_baseline_v2"` if
  the parent was `"xgb_baseline"`.
- `hypothesis` (required, 1-3 sentences): what makes THIS fork distinct.
  GOOD: "Same prep as xgb_baseline but with class_weights=balanced and
  scale_pos_weight tuned for the 5.6:1 churn imbalance."
  BAD: "another xgboost run".
- `description` (optional): longer notes if you need them.

## Returns

```json
{
  "id": "<new_experiment_id>",
  "session_id": "<same as parent>",
  "name": "...",
  "hypothesis": "...",
  "state": "created"
}
```

## After forking

The new experiment already has the parent's input datasets attached. The
typical next steps are:
1. `start-training(experiment_id, framework, hyperparams)` — note the new
   experiment_id, NOT the parent's.
2. `register-model(experiment_id, ...)` — closes the fork.

Don't call `register-dataset` again unless you actually re-prepped the data.

## When NOT to fork

- If the prep is genuinely different, create a new experiment from scratch
  (`create-experiment(parent_dataset_ids=[raw_id])`) so the lineage shows
  the divergence at the data layer, not just the model layer.
- If you're refining hyperparams on the SAME model, that's a re-train of
  the same experiment — don't fork.
