---
name: create-experiment
description: Open a new agent-declared experiment under the current session. Returns experiment_id you'll use for subsequent register-dataset/start-training/register-model calls.
when_to_use: Before registering any processed dataset or starting any training run. Each distinct attempt at a problem (different model, different feature set, different hyperparams) is its own experiment.
version: '0.2'
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

## Lineage flow — read this carefully

The lineage canvas wants to draw a clean line:

```
Raw dataset  →  Processed dataset  →  Model
```

That graph is built like this:

1. **`list-project-datasets`** — discover what's already in the project
   (raw uploads + any prior processed versions). You'll get their `id`s.
2. **`create-experiment(name, hypothesis)`** — spawn the experiment.
   **Do NOT pass `parent_dataset_ids` for raw uploads here.** The raw
   dataset is linked to the *processed* version (next step) via
   `register-dataset.parent_dataset_id`, which is the right place for
   that edge. Linking raw directly to the experiment here causes the
   canvas to draw a misleading `raw → model` shortcut.
3. **`register-dataset(experiment_id, role='input', parent_dataset_id=<raw_id>)`**
   — declares the processed parquet/csv your prep step wrote. The
   `parent_dataset_id` is what wires `Raw → Processed` in the graph.
4. **`start-training`** → fit → **`register-model(training_dataset_id=<processed_id>)`**
   — the model row pins itself to the processed dataset id. That's
   what wires `Processed → Model` in the graph.

If you skip the `parent_dataset_id` in step 3 the processed dataset
appears as an orphan node. If you skip the `training_dataset_id` in
step 5, the model can't be drawn connected to its data at all.

## When `parent_dataset_ids` IS appropriate on create-experiment

Only when the experiment derives from another **processed** dataset
that already exists in the project (e.g. you're spinning up a new
model on top of someone else's prep). In that case, pass the
processed id and the canvas will show `Processed-A → Experiment`.
For a fresh raw upload, leave it empty.

## Inputs

- `name` (required): a short label, e.g. `"xgb_baseline"`,
  `"linear_with_interactions"`.
- `hypothesis` (required, 1-3 sentences): what you're trying. The
  whole point of declaring this is so the user can later browse
  experiments and immediately understand why each one was run.
- `description` (optional): longer notes if you want to expand on
  the hypothesis. The lineage canvas already renders `hypothesis` as
  the experiment's primary description, so leave this empty unless
  you have substantive extra context.
- `parent_dataset_ids` (optional, NOT for raw uploads): list of
  `dataset_version_id` integers when the experiment is forking from
  an existing **processed** dataset. Skip this for fresh raw inputs
  — wire those via `register-dataset.parent_dataset_id` in the next
  step.

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

Save the `id` — every subsequent skill call (register-dataset,
start-training, register-model) requires it.

## Lifecycle

The experiment's `state` field walks through:
`created → prepping → training → trained` (or `failed` / `abandoned`
if you bail out). Tools enforce the order — you cannot call
`register-model` before `start-training`.
