---
name: validate-serving-app
description: Static + integration sanity check on a model's Modal serving app before the user clicks Deploy. Catches missing artifact paths, mismatched secrets, undeclared imports, and syntax errors locally — so failures surface here, not as a hung endpoint after deploy.
when_to_use: ALWAYS run after `create-serving-app` (or after any edit to app.py via the inspect/edit panel) and BEFORE telling the user to click Deploy. The pre-flight pays for itself the first time it catches a typo.
version: '0.1'
kind: capability
---

# validate-serving-app

Pre-deploy sanity check. Returns:

```json
{
  "ok": true | false,
  "issues": ["…"],          // hard blockers — deploy will fail
  "warnings": ["…"],        // suggested fixes — deploy may succeed
  "serving_app_path": "...",
  "artifact_uri": "...",
  "artifact_path_in_app": "...",
  "pip_packages": ["..."]
}
```

`ok: false` means the deploy will almost certainly fail. Don't tell
the user to click Deploy until you've fixed the issues. `warnings`
are softer — usually about pip deps that may resolve transitively.

## Inputs

- `model_id` (required): the `RegisteredModel.id` to validate.

## What it checks

1. **`ast.parse` the file.** Syntax errors are a guaranteed deploy
   failure. Surfaced as an issue with line number.
2. **ARTIFACT_PATH exists on the volume.** Reads the literal out of
   the file, strips the in-container `/data/` prefix, calls
   `volume.read(...)` to confirm the file is there. The #1 reason
   endpoints hang silently in production is a wrong artifact path —
   `@modal.enter` raises FileNotFoundError and Modal retries the
   container forever. Catch it here.
3. **Modal secret name matches the canonical.** The file references
   `modal.Secret.from_name("trainable-key-<id>")`; we check the id
   matches the model's id. Drift → 401s that look like an auth bug.
4. **Imports vs `.pip_install(...)`.** Walks every `import X` /
   `from X import …`, compares against the `pip_install` args of the
   image. Anything not covered AND not stdlib AND not transitively
   bundled gets flagged as a warning.

## What it does NOT do

- It does NOT actually run the model. A real prediction smoke test
  requires loading the artifact, which means the agent's sandbox
  needs the same deps as the Modal image — usually overkill. Use
  `modal serve` from the user's machine if a runtime test matters.
- It does NOT verify the X-API-Key value. The Modal secret is
  managed by the deploy pipeline; the value isn't visible to the
  agent.

## Recommended flow

```
create-serving-app(model_id)         # generate the file
validate-serving-app(model_id)       # pre-flight
# if issues: read the file, edit, validate again
# if ok: tell the user to click Deploy
```

When the user pushes back ("the deployed endpoint hangs") run
validate again — chances are the artifact path drifted because they
re-trained the model and the volume path changed.
