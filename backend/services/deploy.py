"""Deployment — turn a registered model into a live Modal web endpoint.

We don't fork-and-deploy a brand-new Modal App per model — that would be
expensive and slow. Instead, every project shares a single Modal App named
`trainable-serving-{project_id}`, and each deployed model corresponds to a
deterministic function name on that app. The function reads the artifact
straight from the Modal volume at request time, so deployments are
near-instant (no rebuild) and rollback is a row delete.

This service stores the URL string the user can curl. Tearing down is
metadata-only here (status='stopped'); the real Modal teardown happens via
`modal app stop` if needed.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from config import settings
from db import async_session
from models import Deployment, RegisteredModel

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "model"


def _modal_app_name(project_id: str) -> str:
    """Modal app names: 1-63 chars, [a-z0-9-]. Modal also enforces a
    subdomain length limit on the deployed URL — when
    `<workspace>--<app>--<fn>.modal.run` exceeds 63 chars Modal hashes
    the label and prints "(label truncated)". To stay well under that,
    we use only the first 12 hex chars of the project UUID — collisions
    here are vanishingly unlikely and the project_id is still
    recoverable via the model row.
    """
    short_pid = project_id.split("-")[0][:12]
    return f"{settings.modal_app_name}-srv-{short_pid}"[:63]


def _modal_function_name(name: str, version: int) -> str:
    return f"{_slugify(name)}-v{version}"


def _build_endpoint_url(app_name: str, fn_name: str) -> str:
    """Produce a best-effort endpoint URL when `modal deploy` either
    isn't available or didn't print one.

    The canonical URL pattern is
    `https://<workspace>--<app>--<fn>.modal.run`. We prefer reading the
    URL straight out of `modal deploy` stdout; this fallback only runs
    when the CLI isn't installed or the parse failed, in which case we
    substitute MODAL_WORKSPACE from the env if set.
    """
    import os

    workspace = os.environ.get("MODAL_WORKSPACE") or "{workspace}"
    return f"https://{workspace}--{app_name}--{fn_name}.modal.run"


# ---------------------------------------------------------------------------
# Serving app codegen
# ---------------------------------------------------------------------------


# Modal GPU values accepted by the `gpu=` arg on @app.cls. Anything
# outside this set gets coerced to None (= CPU-only). Modal accepts
# more shapes (e.g. "A100-40GB:2"), but we expose the common single-
# device variants here — power users can hand-edit the generated
# app.py for fancier configs.
COMPUTE_CHOICES: dict[str, str | None] = {
    "cpu": None,
    "T4": "T4",
    "L4": "L4",
    "A10G": "A10G",
    "A100-40GB": "A100-40GB",
    "A100-80GB": "A100-80GB",
    "H100": "H100",
}


def _normalize_compute(compute: str | None) -> str:
    """Map user-supplied compute label to a key in COMPUTE_CHOICES.
    Falls back to 'cpu' for anything we don't recognize so a misspelled
    value never silently lights up an expensive A100."""
    if not compute:
        return "cpu"
    if compute in COMPUTE_CHOICES:
        return compute
    return "cpu"


def _serving_app_code(
    *,
    app_name: str,
    fn_name: str,
    model_name: str,
    model_version: int,
    artifact_uri: str,
    framework: str,
    feature_columns: list[str] | None,
    target_column: str | None,
    volume_name: str,
    compute: str = "cpu",
    api_secret_name: str | None = None,
) -> str:
    """Render the Modal serving app for a registered model.

    Modal's CLI deploys whatever module the user points it at — we
    write a self-contained Python file the user can read, edit, or
    deploy manually. The endpoint exposes an OpenAPI spec at /docs
    when `docs=True` so the user gets a free Swagger UI.
    """
    feature_cols_repr = repr(feature_columns) if feature_columns else "None"
    target_repr = repr(target_column) if target_column else "None"
    pip_pkgs = [
        "numpy",
        "pandas",
        "scikit-learn",
        "fastapi[standard]",
        "joblib",
    ]
    fw = (framework or "").lower()
    if fw == "xgboost":
        pip_pkgs.append("xgboost")
    elif fw in ("lightgbm", "lgbm"):
        pip_pkgs.append("lightgbm")
    elif fw == "pytorch":
        pip_pkgs.extend(["torch", "torchvision"])
    elif fw in ("transformers", "huggingface"):
        pip_pkgs.extend(["transformers", "torch", "accelerate"])
    pip_args = ", ".join(repr(p) for p in pip_pkgs)
    gpu_value = COMPUTE_CHOICES.get(compute)
    # Render `gpu="T4",` line only when a GPU was requested; for CPU we
    # leave the kwarg out entirely so Modal schedules on a CPU pool.
    gpu_line = f"    gpu={gpu_value!r},\n" if gpu_value else ""
    compute_label = compute if gpu_value else "CPU"

    # When an api_secret_name is supplied, the @app.cls + @app.function
    # both attach the Modal secret (so `os.environ["API_KEY"]` is
    # available inside the container) and the endpoint validates an
    # `X-API-Key` header against it. Skipping this would leave the
    # endpoint open to anyone who learns the URL — Modal endpoints are
    # public by default. Cleanly omitted when no secret is requested
    # (e.g. local debugging deploys).
    secrets_kwarg = (
        f"    secrets=[modal.Secret.from_name({api_secret_name!r})],\n"
        if api_secret_name
        else ""
    )
    # HTTPException is always needed (endpoint raises 500 on
    # model_load_failed). Header only when auth is wired. Pydantic
    # request/response models give Swagger a real schema to render
    # instead of `dict` — they live in every generated app.
    auth_imports = (
        "from fastapi import Header, HTTPException\n"
        "from pydantic import BaseModel, Field\n"
        if api_secret_name
        else "from fastapi import HTTPException\n"
        "from pydantic import BaseModel, Field\n"
    )
    # The endpoint signature lives at 8-space indent (inside a class
    # method); auth_check goes inside the method body at 8 spaces too.
    auth_param = (
        ',\n        x_api_key: str | None = Header(default=None, alias="X-API-Key")'
        if api_secret_name
        else ""
    )
    auth_check = (
        '''
        expected_key = os.environ.get("API_KEY", "")
        if not expected_key or x_api_key != expected_key:
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
'''
        if api_secret_name
        else ""
    )

    auth_doc = (
        "\nAuthentication: every request must include the `X-API-Key` "
        "header. The expected value lives in the Modal secret "
        f"`{api_secret_name}`; the platform creates and rotates this "
        "secret automatically.\n"
        if api_secret_name
        else ""
    )

    # Build a sample record from the model's feature columns so the
    # `request` body in Swagger isn't `dict` with no schema. Without
    # dtypes we can't perfectly type each field, so we emit
    # `dict[str, float | int | None]` and attach a JSON example with
    # zero values for every trained feature. The user can hand-edit the
    # generated app.py to tighten dtypes / add bounds if they want.
    if feature_columns:
        # Stable, alphabetised order — easier to scan in Swagger.
        sample_record = {c: 0 for c in feature_columns}
        feature_list_md = (
            ", ".join(f"`{c}`" for c in feature_columns[:30])
            + (" …" if len(feature_columns) > 30 else "")
        )
    else:
        sample_record = {"feature_a": 0.0, "feature_b": 0.0}
        feature_list_md = "(unknown — register the training dataset's metadata to populate this)"
    sample_response_predictions = "[0]"
    sample_record_repr = repr(sample_record)

    # Resolve the in-container artifact path. The Modal volume is mounted
    # at /data, so volume-relative paths like `/projects/.../model.pkl`
    # become `/data/projects/.../model.pkl` inside the container. But the
    # agent sometimes supplies an already-/data-prefixed path (sandbox
    # mounts the same volume at /data, so a write to
    # `/data/sessions/x/y.pkl` produces an artifact_uri that already has
    # the prefix). Strip the leading `/data/` if present so we don't end
    # up at `/data/data/sessions/...` which doesn't exist and causes
    # @modal.enter to FileNotFoundError-loop forever.
    container_artifact_path = artifact_uri
    if container_artifact_path.startswith("/data/"):
        container_artifact_path = container_artifact_path[len("/data") :]
    container_artifact_path = "/data" + container_artifact_path

    # Pick a loader by file extension + framework. The previous codegen
    # always used pickle which silently fails on:
    #   .json     — XGBoost / lightgbm native format
    #   .ubj      — XGBoost binary
    #   .pt/.pth  — PyTorch state dict
    #   .safetensors — Hugging Face
    # so the container would crash on enter and the endpoint hung.
    ext = container_artifact_path.rsplit(".", 1)[-1].lower() if "." in container_artifact_path else ""
    fw = (framework or "").lower()
    # Each loader_block lives inside `try:` block of `def load(self)`,
    # which is at 12-space indentation in the rendered file. The first
    # line is interpolated directly at column 12, so subsequent lines
    # MUST start with 12 spaces (= `IND`). Getting this wrong yields
    # an IndentationError that crashes the container — use IND
    # consistently below.
    IND = "            "  # 12 spaces (try body)
    IND_NESTED = IND + "    "  # 16 spaces (one level deeper)
    if fw == "xgboost" and ext in ("json", "ubj", "bin"):
        loader_block = (
            f'import xgboost as xgb\n'
            f'{IND}booster = xgb.Booster()\n'
            f'{IND}booster.load_model(ARTIFACT_PATH)\n'
            f'{IND}self._model = booster\n'
            f'{IND}self._feature_cols = FEATURE_COLUMNS'
        )
    elif fw in ("lightgbm", "lgbm") and ext in ("txt", "model"):
        loader_block = (
            f'import lightgbm as lgb\n'
            f'{IND}self._model = lgb.Booster(model_file=ARTIFACT_PATH)\n'
            f'{IND}self._feature_cols = FEATURE_COLUMNS'
        )
    elif ext == "joblib":
        loader_block = (
            f'import joblib\n'
            f'{IND}blob = joblib.load(ARTIFACT_PATH)\n'
            f'{IND}if isinstance(blob, dict) and "model" in blob:\n'
            f'{IND_NESTED}self._model = blob["model"]\n'
            f'{IND_NESTED}self._feature_cols = blob.get("feature_cols") or FEATURE_COLUMNS\n'
            f'{IND}else:\n'
            f'{IND_NESTED}self._model = blob\n'
            f'{IND_NESTED}self._feature_cols = FEATURE_COLUMNS'
        )
    else:
        # Default: pickle (the .pkl/.pickle case).
        loader_block = (
            f'import pickle\n'
            f'{IND}with open(ARTIFACT_PATH, "rb") as f:\n'
            f'{IND_NESTED}blob = pickle.load(f)\n'
            f'{IND}if isinstance(blob, dict) and "model" in blob:\n'
            f'{IND_NESTED}self._model = blob["model"]\n'
            f'{IND_NESTED}self._feature_cols = blob.get("feature_cols") or FEATURE_COLUMNS\n'
            f'{IND}else:\n'
            f'{IND_NESTED}self._model = blob\n'
            f'{IND_NESTED}self._feature_cols = FEATURE_COLUMNS'
        )

    # Triple-brace for f-string vs Python source dict literals.
    return f'''"""Modal serving app for {model_name} v{model_version}.

Generated by Trainable's `create-serving-app` skill.
Compute target: {compute_label}.
Edit freely — the deploy button will redeploy whatever is on disk.

Local deploy:
    modal deploy {fn_name}_app.py

After deploy, Modal prints the live URL. Open it with `/docs` appended
to see the auto-generated Swagger UI; POST to the root URL to predict.
{auth_doc}"""

import os
import modal
{auth_imports}

app = modal.App({app_name!r})

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install({pip_args})
)

# Shared Modal volume that holds every project's datasets and model
# artifacts. We mount it at /data so the artifact path resolves.
volume = modal.Volume.from_name({volume_name!r}, create_if_missing=False)

ARTIFACT_PATH = {container_artifact_path!r}
FEATURE_COLUMNS = {feature_cols_repr}
TARGET_COLUMN = {target_repr}


# -----------------------------------------------------------------------------
# Request / response contract — what Swagger renders at /docs.
# Each "record" is a dict of trained feature values. We don't store
# per-feature dtypes, so the type is `dict[str, float | int | None]` —
# tighten by hand in this file if you want stricter validation.
# Trained feature columns: {feature_list_md}
# -----------------------------------------------------------------------------
class PredictRequest(BaseModel):
    records: list[dict] = Field(
        ...,
        description=(
            "Batch of records to predict on. Each record is a dict whose "
            "keys are the trained feature columns (see schema example). "
            "Unknown keys are dropped; missing keys are treated as 0."
        ),
        examples=[[{sample_record_repr}]],
    )


class PredictResponse(BaseModel):
    predictions: list = Field(
        ...,
        description="One element per input record. Class label for classifiers, regressed value for regressors.",
        examples=[{sample_response_predictions}],
    )
    model: str = Field({model_name!r}, description="Model name.")
    version: int = Field({model_version}, description="Model version.")


@app.cls(
    image=image,
    volumes={{"/data": volume}},
{gpu_line}{secrets_kwarg}    scaledown_window=120,
)
class Model:
    # `_load_error` is set when @modal.enter fails so subsequent
    # predict() calls return a 500 with the reason instead of hanging
    # the container in a `@modal.enter`-loop. Without this, a typo in
    # ARTIFACT_PATH or a missing dep would make the endpoint silently
    # hang on every request — which is what the user hit before.
    _load_error: str | None = None

    @modal.enter()
    def load(self):
        try:
            {loader_block}
        except Exception as e:  # pragma: no cover — runs in the Modal container
            self._load_error = f"{{type(e).__name__}}: {{e}} (artifact={{ARTIFACT_PATH}})"
            self._model = None
            self._feature_cols = None
            print("[serving] @modal.enter load failed:", self._load_error)

    # The HTTP endpoint lives directly on the class (single container,
    # single boot). The previous codegen used `@app.cls` + a separate
    # `@app.function` that called `model.predict.remote()` — that
    # required two containers to start in series and routinely tripped
    # Modal's 30s "shutting down" grace period, leaving curl hanging.
    @modal.fastapi_endpoint(method="POST", docs=True)
    def {fn_name.replace("-", "_")}(
        self,
        body: PredictRequest{auth_param},
    ) -> PredictResponse:
        """Run inference on a batch of records.

        **Authentication.** Send your `X-API-Key` header on every
        request. Get it from the model card on `/models` (Show Key →
        Copy).

        **Body.** A JSON object with a `records` list. Each record is a
        dict whose keys are the model's trained feature columns. The
        Swagger Try-It-Out panel above is pre-filled with the right
        feature names + zero values — edit those values, click Execute.

        **Response.** A list of predictions (one per record) plus the
        model identity. For classifiers this is a class label; for
        regressors a numeric value.

        Trained feature columns ({len(feature_columns) if feature_columns else "?"}): {feature_list_md}.
        """{auth_check}
        if self._load_error or self._model is None:
            raise HTTPException(
                status_code=500,
                detail=self._load_error or "model failed to load",
            )
        records = body.records
        if not records:
            raise HTTPException(status_code=400, detail="`records` is empty.")

        import pandas as pd

        if isinstance(records, dict):
            records = [records]
        df = pd.DataFrame(records)
        if self._feature_cols:
            df = df[[c for c in self._feature_cols if c in df.columns]]
        # XGBoost low-level Booster + LightGBM Booster need DMatrix /
        # raw matrix, not a DataFrame. Sklearn / xgb.XGBClassifier /
        # other "high-level" estimators accept the DataFrame directly.
        # Sniff the type at predict time so the same code path works
        # for any framework.
        try:
            import xgboost as _xgb  # noqa: F401
            if isinstance(self._model, _xgb.Booster):
                input_obj = _xgb.DMatrix(df)
            else:
                input_obj = df
        except Exception:
            input_obj = df
        preds = self._model.predict(input_obj)
        return PredictResponse(
            predictions=[p.item() if hasattr(p, "item") else p for p in preds],
            model={model_name!r},
            version={model_version},
        )
'''


def _api_secret_name(model_id: str) -> str:
    """Modal secret name for a model's auth key. 12 hex chars from the
    model uuid keeps it well under Modal's 64-char secret-name limit and
    is uniquely deterministic per model.
    """
    return f"trainable-key-{model_id.replace('-', '')[:12]}"


def _generate_api_key() -> str:
    """43-char URL-safe random token. Same primitive used by Django's
    SECRET_KEY default and `secrets.compare_digest`-friendly."""
    import secrets

    return secrets.token_urlsafe(32)


async def generate_serving_app(
    model_id: str,
    *,
    compute: str = "cpu",
    enable_auth: bool = True,
) -> dict[str, Any]:
    """Write a Modal serving app to the volume for the given model and
    record the path on the model row.

    `compute` is one of `cpu | T4 | L4 | A10G | A100-40GB | A100-80GB
    | H100` and controls the `gpu=` arg on `@app.cls`. Anything else
    falls back to CPU. Re-running this with a different value is the
    canonical way to flip a deployment from CPU to GPU — the file is
    overwritten in place and the next click of Deploy ships it.

    `enable_auth=True` (the default) wires an `X-API-Key` header check
    into the generated endpoint and references the Modal secret named
    `_api_secret_name(model_id)`. The secret itself isn't created here
    — that happens at deploy time so we have the CLI side-effects in
    one place.

    Returns the {model_id, serving_app_path, code_preview, compute,
    api_secret_name} shape the skill handler emits to the agent.
    """
    from services.volume import write_to_volume

    compute = _normalize_compute(compute)
    secret_name = _api_secret_name(model_id) if enable_auth else None

    async with async_session() as db:
        model = (
            await db.execute(
                select(RegisteredModel).where(RegisteredModel.id == model_id)
            )
        ).scalar_one_or_none()
        if not model:
            raise ValueError(f"Model {model_id} not found")

        app_name = _modal_app_name(model.project_id)
        fn_name = _modal_function_name(model.name, model.version)

        # Pull feature_columns / target_column from the training dataset's
        # metadata if it's available — the predict() endpoint needs them
        # to project incoming JSON in the right column order. Falls back
        # to None so the model picks them up from the pickled blob.
        feature_cols: list[str] | None = None
        target_col: str | None = None
        try:
            from models import DatasetVersion

            train_id = (model.dataset_refs or {}).get("train", {}).get("dataset_id")
            if train_id:
                dv = (
                    await db.execute(
                        select(DatasetVersion).where(DatasetVersion.id == int(train_id))
                    )
                ).scalar_one_or_none()
                if dv and dv.dataset_metadata:
                    md = dv.dataset_metadata
                    if isinstance(md, dict):
                        feature_cols = md.get("feature_columns") or None
                        target_col = md.get("target_column") or None
        except Exception as e:
            logger.debug("[deploy] could not resolve training metadata: %s", e)

        code = _serving_app_code(
            app_name=app_name,
            fn_name=fn_name,
            model_name=model.name,
            model_version=model.version,
            artifact_uri=model.artifact_uri,
            framework=model.framework or "sklearn",
            feature_columns=feature_cols,
            target_column=target_col,
            volume_name=settings.modal_volume_name,
            compute=compute,
            api_secret_name=secret_name,
        )

        # Park the file alongside the artifact so it's easy to find +
        # version-pinned to (project, model, version).
        app_path = (
            f"/projects/{model.project_id}/models/{model.name}/v{model.version}/app.py"
        )
        # `write_to_volume` writes text content via mode="w"; pass the
        # rendered source as a str rather than utf-8 bytes.
        await write_to_volume(code, app_path)

        model.serving_app_path = app_path
        await db.commit()

        return {
            "model_id": model_id,
            "serving_app_path": app_path,
            "modal_app": app_name,
            "modal_function": fn_name,
            "compute": compute,
            "api_secret_name": secret_name,
            "code_preview": code[:600] + ("…" if len(code) > 600 else ""),
        }


async def deploy_model(
    model_id: str,
    *,
    compute: str | None = None,
) -> dict[str, Any]:
    """Run `modal deploy` for the model's serving app and persist the
    real URL printed on stdout.

    `compute` chooses the Modal target:
      None / "cpu" → CPU pool (no `gpu=` arg)
      "T4" / "L4" / "A10G" / "A100-40GB" / "A100-80GB" / "H100" →
        regenerate app.py with that GPU set on @app.cls before deploy.

    The call FAILS LOUDLY when the model has no `serving_app_path`
    set yet — the user has to ask an agent to call
    `create-serving-app` first.

    If a live deployment already exists with the SAME compute target,
    we short-circuit and return it (idempotent refresh). When the user
    picks a different compute, we proceed to regenerate + redeploy and
    Modal silently rolls the existing app.

    Returns the Deployment row dict. Raises ValueError if the model is
    missing or has no serving app.
    """
    compute_norm = _normalize_compute(compute)

    async with async_session() as db:
        model = (
            await db.execute(
                select(RegisteredModel).where(RegisteredModel.id == model_id)
            )
        ).scalar_one_or_none()
        if not model:
            raise ValueError(f"Model {model_id} not found")

        if not model.serving_app_path:
            raise ValueError(
                "This model has no serving app yet. Ask an agent to run "
                "`create-serving-app` against it, then click Deploy again. "
                "(The serving app is the Modal Python file we ship — "
                "without it there's nothing to deploy.)"
            )

        app_name = _modal_app_name(model.project_id)
        fn_name = _modal_function_name(model.name, model.version)

        # We always proceed to a fresh `modal deploy` so the URL
        # reflects the current app.py. Previous code short-circuited
        # when compute matched, which masked regenerated apps and let
        # the UI keep showing a stale URL after the agent edited the
        # serving file. Old live rows are marked `superseded` further
        # down so the catalog only shows one live row per model.
        existing = (
            await db.execute(
                select(Deployment).where(
                    Deployment.model_id == model_id,
                    Deployment.status == "live",
                )
            )
        ).scalar_one_or_none()

    # Ensure the model has an API key + the Modal secret exists. The
    # key is generated once per model and reused across redeploys
    # (incl. compute changes) so clients don't have to update their
    # X-API-Key header every time. `rotate-key` regenerates it.
    async with async_session() as db:
        model = (
            await db.execute(
                select(RegisteredModel).where(RegisteredModel.id == model_id)
            )
        ).scalar_one()
        if not model.api_key:
            model.api_key = _generate_api_key()
            await db.commit()
        api_key_value = model.api_key
    secret_name = _api_secret_name(model_id)
    try:
        await _ensure_modal_secret(secret_name, api_key_value)
    except Exception as e:
        # Surface but don't block — the deploy still ships, but
        # without the secret the endpoint will 401 on every request.
        # Better to surface a deploy_row.error than silently allow an
        # open endpoint or a broken auth check.
        logger.exception("Modal secret create failed: %s", e)
        async with async_session() as db:
            row = Deployment(
                id=str(uuid.uuid4()),
                model_id=model_id,
                endpoint_url=None,
                status="failed",
                error=f"could not create Modal secret {secret_name}: {e}",
                modal_app=app_name,
                modal_function=fn_name,
                compute=compute_norm,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.to_dict()

    # Regenerate app.py with the chosen compute BEFORE invoking the
    # CLI — the file on disk is the source of truth for `modal deploy`.
    # Done outside the previous async-session block so the regen
    # writes its own commit.
    await generate_serving_app(model_id, compute=compute_norm)

    async with async_session() as db:
        # Re-fetch the model so we have the freshly-updated
        # serving_app_path (write_to_volume can rewrite atop it).
        model = (
            await db.execute(
                select(RegisteredModel).where(RegisteredModel.id == model_id)
            )
        ).scalar_one()

        try:
            url = await _run_modal_deploy(model.serving_app_path, app_name)
            status = "live"
            error_text = None
        except Exception as e:
            logger.exception("Modal deploy failed: %s", e)
            status = "failed"
            error_text = str(e)
            # Fallback URL stub still written so the user can see *what
            # the URL would have been* even when deploy itself failed.
            url = _build_endpoint_url(app_name, fn_name)

        # Mark any prior live deployment as superseded so the UI doesn't
        # show two "live" rows for the same model. Always do this on
        # successful redeploy — Modal rolls the underlying app, so the
        # old row's URL may not even point at the latest container.
        if status == "live":
            sup = (
                await db.execute(
                    select(Deployment).where(
                        Deployment.model_id == model_id,
                        Deployment.status == "live",
                    )
                )
            ).scalars().all()
            for s in sup:
                s.status = "superseded"
                s.updated_at = datetime.now(timezone.utc).isoformat()

        row = Deployment(
            id=str(uuid.uuid4()),
            model_id=model_id,
            endpoint_url=url if status == "live" else None,
            status=status,
            error=error_text,
            modal_app=app_name,
            modal_function=fn_name,
            compute=compute_norm,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.to_dict()


async def _ensure_modal_secret(secret_name: str, value: str) -> None:
    """Create-or-replace the named Modal secret with `API_KEY=<value>`.

    Modal's CLI rejects `secret create` if the name already exists, so
    we delete-then-create. Both calls tolerate missing-token / network
    errors — on failure the deploy still goes through, but the
    container will see no API_KEY and the auth check rejects every
    request, which is the safe default. We surface the failure as a
    ValueError so the caller can decide whether to abort.
    """
    import asyncio
    import shutil

    if not shutil.which("modal"):
        raise ValueError(
            "modal CLI not found in PATH. Install it (`pip install modal`) "
            "and run `modal token new` to enable deploy-time secret creation."
        )

    # Delete existing — ignore failure (it might not exist yet).
    try:
        proc = await asyncio.create_subprocess_exec(
            "modal",
            "secret",
            "delete",
            secret_name,
            "--yes",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=20)
    except Exception:
        pass

    # Create fresh secret. `modal secret create <name> KEY=value` writes
    # the secret atomically; any failure here is unrecoverable for this
    # deploy.
    proc = await asyncio.create_subprocess_exec(
        "modal",
        "secret",
        "create",
        secret_name,
        f"API_KEY={value}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        out = out_bytes.decode("utf-8", errors="replace")
        raise ValueError(
            f"modal secret create exited {proc.returncode}: {out[-500:]}"
        )


async def _run_modal_deploy(serving_app_path: str, app_name: str) -> str:
    """Invoke `modal deploy` and return the live web-endpoint URL.

    `modal deploy` runs against the user's locally-configured workspace
    (`MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` in env, or `~/.modal.toml`).
    Stdout contains lines like:

        ✓ Created web function predict => https://ws--app--predict.modal.run

    We grep the URL out. If multiple are printed we take the first
    (single-endpoint apps are the common case here).
    """
    import asyncio
    import os
    import re
    import shutil

    from services.volume import read_volume_file_async

    if not shutil.which("modal"):
        raise RuntimeError(
            "modal CLI not found in PATH. Install it (`pip install modal`) and "
            "configure auth via `modal token new` to deploy from this server."
        )

    # Materialize the serving app from the volume into a temp file so
    # modal CLI can read it. The volume is mounted inside Modal
    # containers, but the CLI itself runs on the box and reads local
    # files.
    code = await read_volume_file_async(serving_app_path)
    import tempfile

    tmp = tempfile.NamedTemporaryFile(mode="wb", suffix="_app.py", delete=False)
    tmp.write(code)
    tmp.flush()
    tmp.close()
    local_path = tmp.name

    try:
        env = os.environ.copy()
        proc = await asyncio.create_subprocess_exec(
            "modal",
            "deploy",
            local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        out = out_bytes.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(
                f"modal deploy exited {proc.returncode}.\n--- output ---\n{out[-2000:]}"
            )

        # Modal prints the deployed URL inside a Rich-style panel that
        # soft-wraps long URLs across lines (and surrounds them with
        # box-drawing chars + emojis). To recover the URL we strip
        # everything outside the URL alphabet — `https://...modal.run`
        # only uses letters, digits, dot, slash, colon, hyphen, and
        # underscore so any whitelist over those preserves the URL
        # while collapsing the noise.
        clean = re.sub(r"[^a-zA-Z0-9\-_./:]", "", out)
        # Modal URLs come in two flavors:
        #   <workspace>--<app>--<fn>.modal.run        (full)
        #   <workspace>--<truncated_label>.modal.run  (when over 63 chars)
        # The regex below accepts both. With the shortened app name
        # above we should always hit the full form, but we keep the
        # fallback so deployments under legacy long names still parse.
        match = re.search(
            r"https://[a-z0-9_-]+--[a-z0-9_-]+(?:--[a-z0-9_-]+)?\.modal\.run",
            clean,
        )
        if not match:
            raise RuntimeError(
                "modal deploy succeeded but no URL was printed. "
                f"Tail of output:\n{out[-1500:]}"
            )
        url = match.group(0)
        logger.info("[deploy] modal deploy succeeded for %s → %s", app_name, url)
        return url
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass


async def rotate_api_key(model_id: str) -> dict:
    """Generate a fresh API key, replace the Modal secret, and return
    the new key.

    The serving app already references the secret BY NAME, so the
    running endpoint picks up the new key on its next cold start. To
    force an immediate cutover the user can click Redeploy after.
    """
    async with async_session() as db:
        model = (
            await db.execute(
                select(RegisteredModel).where(RegisteredModel.id == model_id)
            )
        ).scalar_one_or_none()
        if not model:
            raise ValueError(f"Model {model_id} not found")
        new_key = _generate_api_key()
        secret_name = _api_secret_name(model_id)
        await _ensure_modal_secret(secret_name, new_key)
        model.api_key = new_key
        await db.commit()
        return {
            "model_id": model_id,
            "api_key": new_key,
            "modal_secret": secret_name,
            "note": (
                "Running containers cache the previous secret value — "
                "redeploy or wait for cold-start to roll the new key."
            ),
        }


async def stop_deployment(deployment_id: str) -> dict:
    """Mark a deployment stopped + run `modal app stop` if the CLI is
    configured. We always update the DB row even when the subprocess
    fails so the UI can recover from "modal token expired" without
    leaving a row stuck in `live`.
    """
    async with async_session() as db:
        row = (
            await db.execute(select(Deployment).where(Deployment.id == deployment_id))
        ).scalar_one_or_none()
        if not row:
            return {"ok": False, "error": "Deployment not found"}

        modal_app = row.modal_app
        # Best-effort Modal teardown. Failures here don't block the row
        # update — the user can `modal app stop` from a terminal as a
        # fallback if needed.
        if modal_app:
            try:
                await _run_modal_app_stop(modal_app)
            except Exception as e:
                logger.warning("[deploy] modal app stop failed for %s: %s", modal_app, e)
                row.error = f"modal app stop failed: {e}"

        row.status = "stopped"
        row.updated_at = datetime.now(timezone.utc).isoformat()
        await db.commit()
        await db.refresh(row)
        return row.to_dict()


async def _run_modal_app_stop(app_name: str) -> None:
    """Run `modal app stop <app_name>` in a subprocess. Raises on
    nonzero exit. Times out after 30s — `app stop` is fast.
    """
    import asyncio
    import shutil

    if not shutil.which("modal"):
        raise RuntimeError("modal CLI not found in PATH")
    proc = await asyncio.create_subprocess_exec(
        "modal",
        "app",
        "stop",
        app_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        out = out_bytes.decode("utf-8", errors="replace")
        raise RuntimeError(f"modal app stop exited {proc.returncode}: {out[-500:]}")


