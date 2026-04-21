"""Extract rich metadata from processed parquet files after prep completes.

Populates the ProcessedDatasetMeta record automatically — does not rely on agent cooperation.
If the agent produced metadata.json, uses it to fill structured fields (target, features).
Otherwise falls back to heuristics.
"""

import io
import json
import logging

import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import select as sa_select

from db import async_session
from models import Artifact, ProcessedDatasetMeta
from services.volume import listdir_async, read_volume_file_async, reload_volume_async

logger = logging.getLogger(__name__)


async def extract_and_store_metadata(session_id: str, experiment_id: str):
    """Read parquet files from Modal Volume, compute metadata, persist to DB."""

    await reload_volume_async()

    # Discover parquet split paths via Artifact DB, falling back to a session scan.
    wanted = {"train.parquet", "val.parquet", "test.parquet", "metadata.json"}
    paths: dict[str, str] = {}
    try:
        async with async_session() as db:
            rows = await db.execute(
                sa_select(Artifact.name, Artifact.path).where(
                    Artifact.session_id == session_id,
                    Artifact.name.in_(list(wanted)),
                )
            )
            for name, p in rows.all():
                paths.setdefault(name, p)
    except Exception as e:
        logger.debug(f"[META] Artifact lookup failed: {e}")

    missing = wanted - set(paths.keys())
    if missing:
        try:
            for entry in await listdir_async(f"/sessions/{session_id}", recursive=True):
                if entry.type.name != "FILE":
                    continue
                base = entry.path.rsplit("/", 1)[-1]
                if base in missing and base not in paths:
                    paths[base] = entry.path
        except Exception as e:
            logger.debug(f"[META] Session scan failed: {e}")

    # Read parquet splits
    splits = {}
    for split_name in ["train", "val", "test"]:
        key = f"{split_name}.parquet"
        path = paths.get(key)
        if not path:
            logger.info(f"[META] {key} not found in session {session_id}")
            continue
        try:
            raw = await read_volume_file_async(path)
            splits[split_name] = {"path": path, "data": raw}
        except Exception:
            logger.info(f"[META] {key} not readable at {path}")

    if not splits:
        logger.warning(f"[META] No parquet splits found for session {session_id}")
        return

    # Extract schema from first split using pyarrow (no full scan)
    first_data = list(splits.values())[0]["data"]
    pf = pq.ParquetFile(io.BytesIO(first_data))
    schema = pf.schema_arrow

    columns_info = []
    for field in schema:
        columns_info.append(
            {
                "name": field.name,
                "dtype": str(field.type),
                "nullable": field.nullable,
            }
        )

    # Row counts per split
    total_rows = 0
    train_rows = val_rows = test_rows = 0
    output_files = []

    for split_name, split_info in splits.items():
        pf = pq.ParquetFile(io.BytesIO(split_info["data"]))
        num_rows = pf.metadata.num_rows
        total_rows += num_rows

        if split_name == "train":
            train_rows = num_rows
        elif split_name == "val":
            val_rows = num_rows
        elif split_name == "test":
            test_rows = num_rows

        output_files.append(
            {
                "name": f"{split_name}.parquet",
                "rows": num_rows,
                "path": split_info["path"],
                "size_bytes": len(split_info["data"]),
            }
        )

    # Try to read agent-produced metadata.json
    agent_meta = None
    meta_path = paths.get("metadata.json")
    if meta_path:
        try:
            meta_raw = await read_volume_file_async(meta_path)
            agent_meta = json.loads(meta_raw.decode("utf-8", errors="replace"))
            logger.info(f"[META] Loaded agent metadata.json for session {session_id}")
        except Exception:
            logger.info("[META] metadata.json unreadable, using heuristics")
    else:
        logger.info("[META] No agent metadata.json found, using heuristics")

    # Determine target and features from agent metadata or heuristics
    all_col_names = [c["name"] for c in columns_info]
    target_column = None
    feature_columns = list(all_col_names)

    if agent_meta:
        target_column = agent_meta.get("target_column")
        if agent_meta.get("features"):
            feature_columns = agent_meta["features"]
    else:
        # Heuristic: common target column names
        target_hints = [
            "target",
            "label",
            "class",
            "y",
            "outcome",
            "species",
            "price",
            "salary",
        ]
        for hint in target_hints:
            for col in all_col_names:
                if col.lower() == hint or col.lower().endswith(f"_{hint}"):
                    target_column = col
                    break
            if target_column:
                break

    if target_column and target_column in feature_columns:
        feature_columns = [c for c in feature_columns if c != target_column]

    # Compute quality stats from train split
    quality_stats = {}
    if "train" in splits:
        try:
            train_df = pd.read_parquet(io.BytesIO(splits["train"]["data"]))

            missing_pct = (train_df.isnull().sum() / len(train_df) * 100).to_dict()
            numeric_cols = train_df.select_dtypes(include=["number"]).columns.tolist()
            numeric_summary = {}
            for col in numeric_cols[:20]:  # Cap at 20 columns for sanity
                numeric_summary[col] = {
                    "mean": round(float(train_df[col].mean()), 4),
                    "std": round(float(train_df[col].std()), 4),
                    "min": round(float(train_df[col].min()), 4),
                    "max": round(float(train_df[col].max()), 4),
                }

            quality_stats = {
                "missing_pct": {k: round(v, 2) for k, v in missing_pct.items()},
                "duplicate_rows": int(train_df.duplicated().sum()),
                "numeric_summary": numeric_summary,
                "n_numeric_cols": len(numeric_cols),
                "n_categorical_cols": len(
                    train_df.select_dtypes(include=["object", "category"]).columns
                ),
            }
        except Exception as e:
            logger.warning(f"[META] Could not compute quality stats: {e}")

    # Collect source file paths
    source_files = []
    try:
        dataset_dir = f"/datasets/{experiment_id}"
        for entry in await listdir_async(dataset_dir, recursive=True):
            if entry.type.name == "FILE":
                source_files.append(entry.path)
    except Exception:
        pass

    # Persist to DB (upsert — update if already exists for this session)

    async with async_session() as db:
        result = await db.execute(
            sa_select(ProcessedDatasetMeta).where(
                ProcessedDatasetMeta.session_id == session_id
            )
        )
        meta = result.scalar_one_or_none()

        if meta:
            meta.experiment_id = experiment_id
            meta.columns = columns_info
            meta.feature_columns = feature_columns
            meta.target_column = target_column
            meta.total_rows = total_rows
            meta.train_rows = train_rows
            meta.val_rows = val_rows
            meta.test_rows = test_rows
            meta.quality_stats = quality_stats
            meta.source_files = source_files
            meta.output_files = output_files
        else:
            meta = ProcessedDatasetMeta(
                session_id=session_id,
                experiment_id=experiment_id,
                columns=columns_info,
                feature_columns=feature_columns,
                target_column=target_column,
                total_rows=total_rows,
                train_rows=train_rows,
                val_rows=val_rows,
                test_rows=test_rows,
                quality_stats=quality_stats,
                source_files=source_files,
                output_files=output_files,
            )
            db.add(meta)
        await db.commit()
        logger.info(
            f"[META] Stored metadata for session {session_id}: {total_rows} rows, {len(columns_info)} cols"
        )
