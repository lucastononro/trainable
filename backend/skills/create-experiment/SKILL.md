---
name: create-experiment
description: Open a new agent-declared experiment under the current session. Returns experiment_id you'll use for subsequent register-dataset/start-training/register-model calls.
when_to_use: Before registering any processed dataset or starting any training run. Each distinct attempt at a problem (different model, different feature set, different hyperparams) is its own experiment.
version: '0.1'
kind: capability
---

# create-experiment

Opens a new experiment inside the current session. An experiment is a
declared bundle of `(processed_dataset, model, metrics, hypothesis)` —
your training run's "registration unit."

A single chat session typically hosts many experiments. Re-use one
experiment only if you're refining the same model on the same dataset.
For a different model, a different feature set, or a different prep
strategy, **create a new experiment**.

## Inputs

- `name` (required): a short label, e.g. `"xgb_baseline"`, `"linear_with_interactions"`.
- `hypothesis` (required, 1-3 sentences): what you're trying. The whole point of declaring this is so the user can later browse experiments and immediately understand why each one was run.
- `description` (optional): longer notes if you want to expand on the
  hypothesis. The lineage canvas already renders `hypothesis` as the
  experiment's primary description, so leave this empty unless you
  have substantive extra context (failed sub-runs, cross-references,
  follow-up tasks).
- `parent_dataset_ids` (optional): list of `dataset_version_id` integers this experiment will derive from (e.g. the raw dataset id the user uploaded). Use this if you already know which datasets you'll be using before the prep step runs.

## Returns

```json
{
  "id": "<experiment_id>",
  "session_id": "<session_id>",
  "name": "...",
  "hypothesis": "...",
  "state": "created"
}
```

Save the `id` — every subsequent skill call (register-dataset, start-training, register-model) requires it.

## Lifecycle

The experiment's `state` field walks through:
`created → prepping → training → trained` (or `failed` / `abandoned` if you bail out).
Tools enforce the order — you cannot call `register-model` before `start-training`.
