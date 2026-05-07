"""Automated post-agent validation for prep and train outputs.

Runs independently of the agent — reads output files directly from Modal Volume
and checks for common ML quality issues. File discovery uses the Artifact DB
table first (populated by publish_artifacts) and falls back to recursively
scanning /sessions/{session_id} — agents are free to organize their workspace
however they like.
"""

import io
import json
import logging

import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import select

from db import async_session
from models import Artifact
from services.volume import (
    listdir_async,
    read_volume_file_async,
    reload_volume_async,
)

logger = logging.getLogger(__name__)


async def _read_volume_file_safe(path: str):
    """Read a file from volume, returning None on failure."""
    try:
        return await read_volume_file_async(path)
    except Exception:
        return None


async def _discover_session_files(
    session_id: str, filenames: set[str]
) -> dict[str, str]:
    """Find absolute paths for given basenames inside a session workspace.

    Checks the Artifact DB first (cheap, authoritative for registered
    outputs). Falls back to a recursive volume scan for anything not yet
    indexed. Returns a mapping of basename -> absolute path. Missing names
    are simply absent from the result.
    """
    found: dict[str, str] = {}

    try:
        async with async_session() as db:
            rows = await db.execute(
                select(Artifact.name, Artifact.path).where(
                    Artifact.session_id == session_id,
                    Artifact.name.in_(list(filenames)),
                )
            )
            for name, path in rows.all():
                if name not in found:
                    found[name] = path
    except Exception as e:
        logger.debug("Artifact lookup failed: %s", e)

    missing = filenames - set(found.keys())
    if missing:
        try:
            await reload_volume_async()
            for entry in await listdir_async(f"/sessions/{session_id}", recursive=True):
                if entry.type.name != "FILE":
                    continue
                base = entry.path.rsplit("/", 1)[-1]
                if base in missing and base not in found:
                    found[base] = entry.path
        except Exception as e:
            logger.debug("Scan discovery for %s failed: %s", session_id, e)

    return found


async def validate_prep_output(session_id: str, experiment_id: str) -> dict:
    """Validate prep stage outputs. Returns dict with errors, warnings, passed checks."""
    await reload_volume_async()

    results = {"errors": [], "warnings": [], "passed": [], "stage": "prep"}

    wanted = {"train.parquet", "val.parquet", "test.parquet", "metadata.json"}
    discovered = await _discover_session_files(session_id, wanted)

    # 1. Check parquet files exist and are readable
    splits = {}
    for split_name in ("train", "val", "test"):
        key = f"{split_name}.parquet"
        path = discovered.get(key)
        raw = await _read_volume_file_safe(path) if path else None
        if raw is None:
            results["errors"].append(f"{key} missing or unreadable")
        else:
            splits[split_name] = raw
            results["passed"].append(f"{key} exists ({len(raw)} bytes)")

    if not splits:
        results["errors"].append("No parquet splits found — cannot continue validation")
        return results

    # 2. Read schemas and row counts
    schemas = {}
    row_counts = {}
    for name, raw in splits.items():
        try:
            pf = pq.ParquetFile(io.BytesIO(raw))
            schemas[name] = set((f.name, str(f.type)) for f in pf.schema_arrow)
            row_counts[name] = pf.metadata.num_rows
        except Exception as e:
            results["errors"].append(f"{name}.parquet schema read failed: {e}")

    # 3. Schema consistency across splits
    if len(schemas) > 1:
        ref_name = list(schemas.keys())[0]
        ref_schema = schemas[ref_name]
        for name, schema in schemas.items():
            if name == ref_name:
                continue
            if schema == ref_schema:
                results["passed"].append(f"{name} schema matches {ref_name}")
            else:
                missing = ref_schema - schema
                extra = schema - ref_schema
                msg = f"{name} schema differs from {ref_name}"
                if missing:
                    msg += f" (missing: {[c[0] for c in missing]})"
                if extra:
                    msg += f" (extra: {[c[0] for c in extra]})"
                results["errors"].append(msg)

    # 4. Check for nulls (sample-based for efficiency)
    if "train" in splits:
        try:
            train_df = pd.read_parquet(io.BytesIO(splits["train"]))
            null_counts = train_df.isnull().sum()
            null_cols = null_counts[null_counts > 0]
            if len(null_cols) == 0:
                results["passed"].append("No null values in train split")
            else:
                for col, count in null_cols.items():
                    results["errors"].append(
                        f"Null values in train.{col}: {count} rows"
                    )
        except Exception as e:
            results["warnings"].append(f"Could not check nulls: {e}")

    # 5. Check split ratios
    total = sum(row_counts.values())
    if total > 0 and len(row_counts) == 3:
        train_ratio = row_counts.get("train", 0) / total
        val_ratio = row_counts.get("val", 0) / total
        test_ratio = row_counts.get("test", 0) / total
        if 0.60 <= train_ratio <= 0.80:
            results["passed"].append(
                f"Train ratio {train_ratio:.2f} within expected range"
            )
        else:
            results["warnings"].append(
                f"Train ratio {train_ratio:.2f} outside expected 0.60-0.80"
            )
        if val_ratio < 0.05 or test_ratio < 0.05:
            results["warnings"].append(
                f"Small split detected: val={val_ratio:.2f}, test={test_ratio:.2f}"
            )

    # 6. Check for data leakage (hash-based row overlap)
    if "train" in splits and "test" in splits:
        try:
            train_df = pd.read_parquet(io.BytesIO(splits["train"]))
            test_df = pd.read_parquet(io.BytesIO(splits["test"]))
            # Hash rows for comparison (sample for large datasets)
            sample_size = min(1000, len(train_df), len(test_df))
            train_sample = (
                train_df.sample(n=sample_size, random_state=42)
                if len(train_df) > sample_size
                else train_df
            )
            test_sample = (
                test_df.sample(n=sample_size, random_state=42)
                if len(test_df) > sample_size
                else test_df
            )
            train_hashes = set(pd.util.hash_pandas_object(train_sample).values)
            test_hashes = set(pd.util.hash_pandas_object(test_sample).values)
            overlap = train_hashes & test_hashes
            if len(overlap) == 0:
                results["passed"].append(
                    "No row overlap detected between train and test"
                )
            else:
                results["errors"].append(
                    f"Potential data leakage: {len(overlap)} overlapping row hashes between train and test"
                )
        except Exception as e:
            results["warnings"].append(f"Could not check leakage: {e}")

    # 7. Check for constant columns
    if "train" in splits:
        try:
            train_df = pd.read_parquet(io.BytesIO(splits["train"]))
            constant_cols = [
                col for col in train_df.columns if train_df[col].nunique() <= 1
            ]
            if constant_cols:
                results["warnings"].append(
                    f"Constant columns (zero variance): {constant_cols}"
                )
            else:
                results["passed"].append("No constant columns found")
        except Exception:
            pass

    # 8. Check metadata.json exists
    metadata_path = discovered.get("metadata.json")
    metadata_raw = (
        await _read_volume_file_safe(metadata_path) if metadata_path else None
    )
    if metadata_raw:
        try:
            meta = json.loads(metadata_raw)
            required_keys = ["target_column", "problem_type", "features", "splits"]
            missing_keys = [k for k in required_keys if k not in meta]
            if missing_keys:
                results["warnings"].append(
                    f"metadata.json missing keys: {missing_keys}"
                )
            else:
                results["passed"].append("metadata.json exists with required keys")

            # Check target column exists in splits
            target = meta.get("target_column")
            if target and schemas:
                first_schema_cols = [c[0] for c in list(schemas.values())[0]]
                if target in first_schema_cols:
                    results["passed"].append(f"Target column '{target}' found in data")
                else:
                    results["errors"].append(
                        f"Target column '{target}' not found in parquet columns"
                    )
        except json.JSONDecodeError:
            results["warnings"].append("metadata.json exists but is not valid JSON")
    else:
        results["warnings"].append(
            "metadata.json not found — agent should produce structured metadata"
        )

    return results


async def validate_train_output(session_id: str, experiment_id: str) -> dict:
    """Validate train stage outputs.

    With the agent-declared-experiments redesign, we check the lifecycle
    state machine first — an experiment that called `start-training` but
    never called `register-model` is the canonical "training abandoned"
    signal. We still keep the legacy file-existence checks below as
    soft warnings to surface lingering issues, but they no longer drive
    the pass/fail decision.
    """
    from models import Experiment, ExperimentState, RegisteredModel

    await reload_volume_async()

    results = {"errors": [], "warnings": [], "passed": [], "stage": "train"}

    # ------------------------------------------------------------------
    # 0. Lifecycle gate — checks every agent-declared experiment in the
    # session. With multi-experiment sessions, we evaluate each in turn.
    # ------------------------------------------------------------------
    try:
        async with async_session() as db:
            exps = (
                (
                    await db.execute(
                        select(Experiment).where(Experiment.session_id == session_id)
                    )
                )
                .scalars()
                .all()
            )
            # Cross-check that each `trained` experiment actually has a
            # RegisteredModel row.
            for exp in exps:
                state = exp.state or ExperimentState.CREATED.value
                if state == ExperimentState.TRAINING.value:
                    results["errors"].append(
                        f"CRITICAL: Experiment '{exp.name}' ({exp.id}) called "
                        "start-training but never called register-model. "
                        "Either call register-model with the trained "
                        "artifact path, or this run will be auto-flagged "
                        "as abandoned after the post-stage cleanup."
                    )
                elif state == ExperimentState.CREATED.value:
                    # Created-but-never-trained is fine if the agent only
                    # ran prep; only flag when there's a clear training
                    # intent (the chat is the trainer agent, so we treat
                    # any created experiment without a model as a miss).
                    results["warnings"].append(
                        f"Experiment '{exp.name}' ({exp.id}) was created "
                        "but no training run was started. Call "
                        "start-training + register-model, or close the "
                        "experiment intentionally."
                    )
                elif state == ExperimentState.TRAINED.value:
                    model = (
                        await db.execute(
                            select(RegisteredModel).where(
                                RegisteredModel.experiment_id == exp.id
                            )
                        )
                    ).scalar_one_or_none()
                    if model:
                        results["passed"].append(
                            f"Model registered for experiment "
                            f"'{exp.name}': {model.name} v{model.version}"
                        )
                    else:
                        results["errors"].append(
                            f"Experiment '{exp.name}' marked TRAINED but "
                            "no RegisteredModel row exists. The "
                            "register-model handler may have failed mid-flight."
                        )
                elif state == ExperimentState.ABANDONED.value:
                    results["warnings"].append(
                        f"Experiment '{exp.name}' was abandoned mid-training."
                    )
                elif state == ExperimentState.FAILED.value:
                    results["warnings"].append(
                        f"Experiment '{exp.name}' is marked FAILED."
                    )
            # No declared experiments at all → defer to the legacy
            # file-walk below, which produces its own pass/fail
            # signals based on artifact presence.
    except Exception as e:
        logger.warning("Lifecycle validation skipped: %s", e)

    # ------------------------------------------------------------------
    # 1. Legacy file-existence checks — soft warnings now, not errors.
    # ------------------------------------------------------------------
    # 1. Check model file exists — look up the most recent "model" artifact.
    #    Falls back to a workspace scan for any model extension.
    model_path: str | None = None
    try:
        async with async_session() as db:
            q = (
                select(Artifact)
                .where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "model",
                )
                .order_by(Artifact.created_at.desc())
            )
            art = (await db.execute(q)).scalars().first()
            if art:
                model_path = art.path
    except Exception as e:
        logger.debug("Artifact model lookup failed: %s", e)

    if not model_path:
        try:
            for entry in await listdir_async(f"/sessions/{session_id}", recursive=True):
                if entry.type.name != "FILE":
                    continue
                lower = entry.path.lower()
                if lower.endswith((".pkl", ".joblib", ".pt", ".h5", ".onnx")):
                    model_path = entry.path
                    break
        except Exception:
            pass

    model_raw = await _read_volume_file_safe(model_path) if model_path else None
    if model_raw:
        results["passed"].append(
            f"Model file found: {model_path} ({len(model_raw)} bytes)"
        )
    else:
        results["errors"].append("No model file found in session workspace")

    # 2. Check report exists — Artifact DB first, then scan for a top-level *.md.
    report_raw = None
    try:
        async with async_session() as db:
            q = (
                select(Artifact)
                .where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "report",
                )
                .order_by(Artifact.created_at.desc())
            )
            art = (await db.execute(q)).scalars().first()
            if art:
                report_raw = _read_volume_file_safe(art.path)
    except Exception:
        pass
    if report_raw is None:
        try:
            for entry in await listdir_async(f"/sessions/{session_id}", recursive=True):
                if entry.type.name != "FILE":
                    continue
                if entry.path.endswith(".md"):
                    report_raw = await _read_volume_file_safe(entry.path)
                    if report_raw:
                        break
        except Exception:
            pass
    if report_raw:
        results["passed"].append(f"report.md exists ({len(report_raw)} bytes)")
    else:
        results["warnings"].append("report.md not found")

    # 3. Check metadata.json — discovery helper covers DB then scan.
    discovered = await _discover_session_files(session_id, {"metadata.json"})
    metadata_path = discovered.get("metadata.json")
    metadata_raw = (
        await _read_volume_file_safe(metadata_path) if metadata_path else None
    )
    if metadata_raw:
        try:
            meta = json.loads(metadata_raw)
            if "best_model" in meta and "test_metrics" in meta:
                results["passed"].append("metadata.json has model and test metrics")

                # Check for suspicious metrics (overfitting / broken)
                for metric_name, value in meta.get("test_metrics", {}).items():
                    if isinstance(value, (int, float)):
                        if value == 1.0 and metric_name in (
                            "accuracy",
                            "f1",
                            "r2",
                            "roc_auc",
                        ):
                            results["warnings"].append(
                                f"Perfect {metric_name}=1.0 on test set — possible overfitting"
                            )
                        if value == 0.0 and metric_name in ("accuracy", "f1", "r2"):
                            results["warnings"].append(
                                f"{metric_name}=0.0 on test set — possible broken model"
                            )
            else:
                results["warnings"].append(
                    "metadata.json missing 'best_model' or 'test_metrics'"
                )
        except json.JSONDecodeError:
            results["warnings"].append("metadata.json is not valid JSON")
    else:
        results["warnings"].append("metadata.json not found")

    return results
