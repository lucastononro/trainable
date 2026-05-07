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

## Standard flow

The expected order, every time you start a fresh attempt:

1. **`list-project-datasets`** — discover raw uploads + processed datasets
   already in the project. You'll get their `id`s.
2. **`create-experiment(name, hypothesis, parent_dataset_ids=[raw_id])`** —
   spawn the experiment AND link the raw upload as an input. The lineage
   canvas immediately shows raw → experiment.
3. (data_prep agent) **`register-dataset(experiment_id, parent_dataset_id=raw_id, …)`**
   — register processed splits, chained from the raw.
4. (trainer agent) **`start-training`** → fit → **`register-model`**.

Skipping step 1 or omitting `parent_dataset_ids` in step 2 means the
raw upload doesn't appear connected to your experiment in the canvas
until prep runs and registers the linkage indirectly. Fix this at
experiment-creation time, not later.

## Inputs

- `name` (required): a short label, e.g. `"xgb_baseline"`, `"linear_with_interactions"`.
- `hypothesis` (required, 1-3 sentences): what you're trying. The whole point of declaring this is so the user can later browse experiments and immediately understand why each one was run.
- `description` (optional): longer notes if you want to expand on the
  hypothesis. The lineage canvas already renders `hypothesis` as the
  experiment's primary description, so leave this empty unless you
  have substantive extra context (failed sub-runs, cross-references,
  follow-up tasks).
- `parent_dataset_ids` (STRONGLY RECOMMENDED for the first call in a
  session): list of `dataset_version_id` integers this experiment will
  derive from. Pass the raw upload's id here so the lineage edge raw →
  experiment is recorded immediately, before prep even runs. Discover
  raw ids via `list-project-datasets` first.

  Skip this only if the experiment is purely synthetic (no upstream
  data) or if you're forking — `fork-experiment` inherits the parent's
  linkages automatically.

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
