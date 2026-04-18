"""Data exploration endpoints using DuckDB for querying processed parquet files."""

import io
import logging
import re

import duckdb
import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import async_session, get_db
from models import Artifact, ProcessedDatasetMeta
from services.volume import get_volume, read_volume_file, reload_volume

logger = logging.getLogger(__name__)
router = APIRouter()

# Only allow SELECT statements — block writes, filesystem access, and DDL
_FORBIDDEN_PATTERNS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|CALL|PRAGMA|EXECUTE)\b",
    re.IGNORECASE,
)
_FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|read_text|glob|list_files)\s*\(",
    re.IGNORECASE,
)


def _validate_query(sql: str) -> None:
    """Reject anything that isn't a plain SELECT query."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped.upper().startswith("SELECT"):
        raise HTTPException(status_code=400, detail="Only SELECT queries are allowed")
    if _FORBIDDEN_PATTERNS.search(stripped):
        raise HTTPException(
            status_code=400, detail="Query contains forbidden statements"
        )
    if _FORBIDDEN_FUNCTIONS.search(stripped):
        raise HTTPException(
            status_code=400,
            detail="Query contains forbidden functions (filesystem access)",
        )


class QueryRequest(BaseModel):
    sql: str
    limit: int = 100


def _load_parquet_to_duckdb(
    con: duckdb.DuckDBPyConnection, raw: bytes, table_name: str
):
    """Load parquet bytes into a DuckDB table via pyarrow."""
    arrow_table = pq.read_table(io.BytesIO(raw))  # noqa: F841 — referenced by DuckDB SQL below
    con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM arrow_table")


async def _resolve_split_paths(session_id: str) -> dict[str, str]:
    """Find {train,val,test}.parquet paths for a session.

    Authoritative source is ProcessedDatasetMeta.output_files (populated by
    metadata_extractor.py). Fallbacks: Artifact DB by name, then a recursive
    volume scan. Agents write these files wherever makes sense under
    /sessions/{session_id}/ — callers should not hardcode paths.
    """
    paths: dict[str, str] = {}

    try:
        async with async_session() as db:
            meta_row = await db.execute(
                select(ProcessedDatasetMeta).where(
                    ProcessedDatasetMeta.session_id == session_id
                )
            )
            meta = meta_row.scalar_one_or_none()
            if meta and meta.output_files:
                for entry in meta.output_files:
                    name = entry.get("name")
                    path = entry.get("path")
                    if name and path:
                        paths.setdefault(name, path)

            if not paths:
                rows = await db.execute(
                    select(Artifact.name, Artifact.path).where(
                        Artifact.session_id == session_id,
                        Artifact.name.in_(["train.parquet", "val.parquet", "test.parquet"]),
                    )
                )
                for name, path in rows.all():
                    paths.setdefault(name, path)
    except Exception:
        pass

    missing = {"train.parquet", "val.parquet", "test.parquet"} - set(paths.keys())
    if missing:
        try:
            reload_volume()
            vol = get_volume()
            for entry in vol.listdir(f"/sessions/{session_id}", recursive=True):
                if entry.type.name != "FILE":
                    continue
                base = entry.path.rsplit("/", 1)[-1]
                if base in missing and base not in paths:
                    paths[base] = entry.path
        except Exception:
            pass

    return paths


@router.post("/sessions/{session_id}/prep/query")
async def query_prep_data(session_id: str, body: QueryRequest):
    """Run a read-only DuckDB SQL query against the processed parquet files.

    Available tables: train, val, test (from parquet splits).
    Also creates an all_data view combining all splits with a 'split' column.
    """

    reload_volume()

    split_paths = await _resolve_split_paths(session_id)
    con = duckdb.connect(":memory:")

    try:
        # Load available splits into DuckDB via pyarrow (before disabling external access)
        for split in ["train", "val", "test"]:
            path = split_paths.get(f"{split}.parquet")
            if not path:
                continue
            try:
                raw = read_volume_file(path)
                _load_parquet_to_duckdb(con, raw, split)
            except Exception:
                pass

        tables = [name[0] for name in con.execute("SHOW TABLES").fetchall()]
        if not tables:
            raise HTTPException(status_code=404, detail="No processed data found")

        # Create combined view
        if len(tables) > 1:
            union_sql = " UNION ALL ".join(
                f"SELECT *, '{t}' as split FROM {t}" for t in tables
            )
            con.execute(f"CREATE VIEW all_data AS {union_sql}")

        # Disable filesystem and network access before running user SQL
        con.execute("SET enable_external_access = false")

        # Validate and execute user query with limit enforcement
        sql = body.sql.strip().rstrip(";")
        _validate_query(sql)
        max_limit = min(body.limit, 1000)
        if "LIMIT" not in sql.upper():
            sql += f" LIMIT {max_limit}"

        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        return {
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows),
            "tables_available": tables,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query error: {e}")
    finally:
        con.close()


@router.get("/sessions/{session_id}/prep/preview")
async def preview_prep_data(
    session_id: str,
    split: str = Query("train", pattern="^(train|val|test)$"),
    limit: int = Query(50, le=1000),
):
    """Quick preview of a processed data split (first N rows)."""

    reload_volume()

    split_paths = await _resolve_split_paths(session_id)
    path = split_paths.get(f"{split}.parquet")
    if not path:
        raise HTTPException(status_code=404, detail=f"{split}.parquet not found")
    try:
        raw = read_volume_file(path)
    except Exception:
        raise HTTPException(status_code=404, detail=f"{split}.parquet not readable")

    con = duckdb.connect(":memory:")
    try:
        _load_parquet_to_duckdb(con, raw, split)
        result = con.execute(f"SELECT * FROM {split} LIMIT ?", [limit])
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return {
            "split": split,
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows),
        }
    finally:
        con.close()


@router.get("/sessions/{session_id}/prep/metadata")
async def get_prep_metadata(session_id: str, db: AsyncSession = Depends(get_db)):
    """Get the processed dataset metadata for a session."""

    result = await db.execute(
        select(ProcessedDatasetMeta).where(
            ProcessedDatasetMeta.session_id == session_id
        )
    )
    meta = result.scalar_one_or_none()
    if not meta:
        raise HTTPException(
            status_code=404, detail="No processed dataset metadata found"
        )
    return meta.to_dict()
