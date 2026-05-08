---
name: start-training
description: Mark an experiment as `training`. Call IMMEDIATELY before .fit() runs so the platform knows training started; call register-model when it finishes.
when_to_use: At the moment you're about to invoke .fit() / .train() / equivalent. Do not call earlier (defer until you've finalized hyperparams).
version: '0.1'
kind: capability
---

# start-training

Opens the training window for an experiment. The platform records:
- `experiment.state = 'training'`
- `experiment.started_at = now()`
- `experiment.hyperparams = <your declared hyperparams>` (frozen for reproducibility)

If you call `start-training` and your turn ends without calling
`register-model`, the experiment is auto-flagged **abandoned** by the
post-stage cleanup hook. The user then sees a warning chip in the
sidebar — which is the signal that something went wrong.

## Inputs

- `experiment_id` (required): from `create-experiment`.
- `framework` (required): one of `xgboost | lightgbm | sklearn | pytorch | tensorflow | huggingface | other`.
- `hyperparams` (optional but encouraged): the dict you'll pass to `.fit()`. Saved on the experiment row so the lineage view can show "this model was trained with these hyperparams" without parsing the snapshot manifest.

## Returns

```json
{
  "experiment_id": "<id>",
  "state": "training",
  "started_at": "<ISO timestamp>"
}
```

## Failure modes

- Calling `start-training` on a `trained` experiment is allowed (re-train), but uncommon — typically you should `create-experiment` for a new attempt.
- Calling on a non-existent experiment_id returns an error.
