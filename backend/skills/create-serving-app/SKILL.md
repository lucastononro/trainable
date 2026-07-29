---
name: create-serving-app
description: Generate a Modal serving app (`app.py`) for a registered model so it can be deployed via the Deploy button on /models. Sets `model.serving_app_path` and unlocks the deploy flow.
when_to_use: Right after `register-model`, when the user expects to deploy. Or any time the user asks "deploy this model" — the deploy button is greyed out until this skill has been called for the model.
version: '0.1'
kind: capability
---

# create-serving-app

Generates a Modal Python file (`app.py`) for a registered model and
parks it next to the artifact at
`/projects/{project_id}/models/{name}/v{N}/app.py`. The model row's
`serving_app_path` is set, which is what the Deploy button on
`/models` keys off — until you call this, that button is disabled.

The generated app:

- Mounts the shared `trainable` volume at `/data`.
- `@app.cls` loads the pickle / safetensors at container start.
- A `@modal.fastapi_endpoint(method="POST", docs=True)` exposes
  `predict` with auto-generated Swagger UI at `/docs`.
- Pulls `feature_columns` + `target_column` from the training
  DatasetVersion's metadata so the endpoint projects incoming JSON to
  the right column order.
- **Ships a typed Pydantic contract.** The endpoint takes
  `body: PredictRequest -> PredictResponse` (not raw `dict`), and
  Swagger renders a real schema with the model's trained feature
  columns pre-filled in the Try-It-Out panel as a working example.
  The user can hit Execute without guessing the input shape.

## What the user sees on Swagger

After deploy, browse to `<endpoint_url>/docs`. They see:

1. The endpoint route + the `X-API-Key` header field (when auth is on).
2. **Request body schema** — `PredictRequest.records: list[dict]`
   with an example pre-populated using the actual feature column names
   from training. Swagger renders the example as the default body in
   the Try-It-Out panel. The user fills in real values, clicks
   Execute, sees the response.
3. **Response schema** — `PredictResponse.predictions: list`,
   `model: str`, `version: int`. Each field has a description.

If you want a tighter contract — typed-per-feature instead of a
single `dict` — edit the generated `app.py` directly. Common
upgrades:

```python
class PassengerRecord(BaseModel):
    Pclass: int = Field(..., ge=1, le=3)
    Sex: int = Field(..., ge=0, le=1, description="0=female, 1=male")
    Age: float = Field(..., ge=0, le=120)
    # ...

class PredictRequest(BaseModel):
    records: list[PassengerRecord]
```

That gives Swagger a per-field schema with bounds + descriptions and
returns 422 on bad input automatically — a much more usable contract
for downstream callers.

## Inputs

- `model_id` (required): the `RegisteredModel.id` returned by
  `register-model`.

## Returns

```json
{
  "model_id": "...",
  "serving_app_path": "/projects/.../v1/app.py",
  "modal_app": "trainable-serving-<project_id>",
  "modal_function": "<model_name>-v<N>",
  "code_preview": "..."
}
```

## How the user deploys

1. They click **Deploy** on `/models`. The button is enabled because
   `model.serving_app_path` is now set.
2. The backend runs `modal deploy <serving_app_path>` and parses the
   real URL out of stdout (no more `{workspace}` placeholder).
3. The Deployment row stores the URL; the model card flips to "Copy
   cURL" and the URL+`/docs` link become available.

## When the user wants to customise

The `app.py` is a regular Python file on the volume. The user (or you)
can edit it via `read-session-file` / `execute-code` — the next deploy
ships whatever is on disk. Common edits:

- Add custom preprocessing inside `Model.load`.
- Pin GPU type via `gpu="T4"` on `@app.cls`.
- Increase `container_idle_timeout` for long-running idle periods.
- Add auth via FastAPI middleware.

## Failure modes

- The skill returns an error if the `model_id` doesn't exist.
- It does NOT validate the artifact is actually loadable — that
  surfaces at first request once deployed.

## Modal reference (read these BEFORE editing the generated app)

The generated `app.py` uses a small subset of Modal's surface. When
the user wants to customise (GPU, timeouts, multi-method classes,
secrets, schedules), look up the relevant page rather than guessing:

- **App** — `modal.App`: container for functions and classes.
  https://modal.com/docs/reference/modal.App
- **Image** — pip dependencies + base layers.
  https://modal.com/docs/reference/modal.Image
- **Volume** — persistent filesystem mounted at /data here.
  https://modal.com/docs/reference/modal.Volume
- **Class with @app.cls** — stateful lifecycle (`@modal.enter`,
  `@modal.exit`, `@modal.method`).
  https://modal.com/docs/reference/modal.Cls
- **fastapi_endpoint** — the decorator that gives you a public URL +
  Swagger UI when `docs=True`.
  https://modal.com/docs/reference/modal.fastapi_endpoint
- **GPU types** — `gpu="T4" | "A10G" | "L4" | "A100" | "H100"`.
  https://modal.com/docs/reference/modal.gpu
- **Secrets** — pass API keys / tokens.
  https://modal.com/docs/reference/modal.Secret
- **Reference index** — full table of contents.
  https://modal.com/docs/reference/

Common tweaks the user asks for (and the page that documents them):

| Ask | Page |
| --- | --- |
| "Make it use a GPU" | modal.gpu |
| "It times out before warming" | `scaledown_window` on `@app.cls` |
| "Add an API key check" | modal.Secret + a FastAPI dependency |
| "Schedule predictions every hour" | modal.Schedule / modal.Period |
| "Stream a large response" | StreamingResponse from FastAPI |
