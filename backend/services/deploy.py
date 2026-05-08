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

    # Triple-brace for f-string vs Python source dict literals.
    return f'''"""Modal serving app for {model_name} v{model_version}.

Generated by Trainable's `create-serving-app` skill.
Compute target: {compute_label}.
Edit freely — the deploy button will redeploy whatever is on disk.

Local deploy:
    modal deploy {fn_name}_app.py

After deploy, Modal prints the live URL. Open it with `/docs` appended
to see the auto-generated Swagger UI; POST to the root URL to predict.
"""

import modal

app = modal.App({app_name!r})

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install({pip_args})
)

# Shared Modal volume that holds every project's datasets and model
# artifacts. We mount it at /data so the artifact path resolves.
volume = modal.Volume.from_name({volume_name!r}, create_if_missing=False)

ARTIFACT_PATH = "/data{artifact_uri}"
FEATURE_COLUMNS = {feature_cols_repr}
TARGET_COLUMN = {target_repr}


@app.cls(
    image=image,
    volumes={{"/data": volume}},
{gpu_line}    scaledown_window=120,
)
class Model:
    @modal.enter()
    def load(self):
        import pickle

        with open(ARTIFACT_PATH, "rb") as f:
            blob = pickle.load(f)
        if isinstance(blob, dict) and "model" in blob:
            self._model = blob["model"]
            self._feature_cols = blob.get("feature_cols") or FEATURE_COLUMNS
        else:
            self._model = blob
            self._feature_cols = FEATURE_COLUMNS

    @modal.method()
    def predict(self, records):
        import pandas as pd

        if isinstance(records, dict):
            records = [records]
        df = pd.DataFrame(records)
        if self._feature_cols:
            # Project to the trained feature order; ignore unknown keys.
            df = df[[c for c in self._feature_cols if c in df.columns]]
        preds = self._model.predict(df)
        return [p.item() if hasattr(p, "item") else p for p in preds]


_model = Model()


@app.function(image=image, volumes={{"/data": volume}})
@modal.fastapi_endpoint(method="POST", docs=True)
def {fn_name.replace("-", "_")}(request: dict):
    """Predict on a batch of records.

    Body:
      {{"records": [{{"feature_a": 1.0, "feature_b": 2.0}}, ...]}}

    Returns:
      {{"predictions": [...], "model": "...", "version": ...}}
    """
    records = request.get("records", [])
    if not records:
        return {{"predictions": [], "model": {model_name!r}, "version": {model_version}, "error": "no records"}}
    return {{
        "predictions": _model.predict.remote(records),
        "model": {model_name!r},
        "version": {model_version},
    }}
'''


async def generate_serving_app(
    model_id: str,
    *,
    compute: str = "cpu",
) -> dict[str, Any]:
    """Write a Modal serving app to the volume for the given model and
    record the path on the model row.

    `compute` is one of `cpu | T4 | L4 | A10G | A100-40GB | A100-80GB
    | H100` and controls the `gpu=` arg on `@app.cls`. Anything else
    falls back to CPU. Re-running this with a different value is the
    canonical way to flip a deployment from CPU to GPU — the file is
    overwritten in place and the next click of Deploy ships it.

    Returns the {model_id, serving_app_path, code_preview, compute}
    shape the skill handler emits to the agent.
    """
    from services.volume import write_to_volume

    compute = _normalize_compute(compute)

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

        # Idempotency: if a deployment already exists in 'live' state
        # with the SAME compute target, return it. A different compute
        # is treated as an explicit upgrade — proceed to redeploy.
        existing = (
            await db.execute(
                select(Deployment).where(
                    Deployment.model_id == model_id,
                    Deployment.status == "live",
                )
            )
        ).scalar_one_or_none()
        if existing and (existing.compute or "cpu") == compute_norm:
            return existing.to_dict()

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
        # show two "live" rows for the same model.
        if existing and (existing.compute or "cpu") != compute_norm:
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


