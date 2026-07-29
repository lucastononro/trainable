"""Raw (pre-prep) dataset preview — quick profile of an uploaded file.

Business logic for `routers/data_explorer.py`'s
`GET /projects/{project_id}/datasets/preview` endpoint (thin-routers rule:
validate in the router, profile here).
"""

import datetime
import decimal
import math
import os
import tempfile

import duckdb


def _json_safe(value):
    """Coerce a DuckDB cell value into something JSON-serializable."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, decimal.Decimal):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def _missing_pct(null_percentage) -> float:
    """Normalize DuckDB SUMMARIZE null_percentage across versions.

    Newer DuckDB returns a DECIMAL percent (e.g. 25.00); older versions
    returned a VARCHAR like "25.0%".
    """
    if null_percentage is None:
        return 0.0
    if isinstance(null_percentage, str):
        null_percentage = null_percentage.rstrip("%").strip() or "0"
    pct = float(null_percentage)
    return min(max(pct, 0.0), 100.0)


def profile_raw_file(raw: bytes, suffix: str, limit: int) -> dict:
    """Scan raw file bytes with DuckDB: head rows + per-column quick profile.

    CPU-bound and blocking — always call via `asyncio.to_thread` (issue #93:
    a large sync scan on the event loop freezes SSE for every session).

    Raises `duckdb.Error` on unparseable input — the router maps that to a
    400; anything else is a server-side failure and must surface as a 500.
    """
    tmp_path: str | None = None
    con = duckdb.connect(":memory:")
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        reader = "read_parquet" if suffix == ".parquet" else "read_csv_auto"
        # Materialize before disabling external access so user-visible
        # queries below never touch the filesystem.
        con.execute(f"CREATE TABLE raw AS SELECT * FROM {reader}(?)", [tmp_path])
        con.execute("SET enable_external_access = false")

        row_count: int = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]

        # One-scan profile: dtype, approx cardinality, and missing % per column.
        summary = con.execute("SUMMARIZE raw")
        summary_cols = [d[0] for d in summary.description]
        columns = []
        for row in summary.fetchall():
            info = dict(zip(summary_cols, row))
            columns.append(
                {
                    "name": info["column_name"],
                    "dtype": info["column_type"],
                    "missing_pct": _missing_pct(info.get("null_percentage")),
                    "unique_count": int(info.get("approx_unique") or 0),
                }
            )

        head = con.execute("SELECT * FROM raw LIMIT ?", [limit])
        head_columns = [d[0] for d in head.description]
        head_rows = [[_json_safe(v) for v in row] for row in head.fetchall()]

        return {
            "row_count": row_count,
            "column_count": len(head_columns),
            "columns": columns,
            "head_columns": head_columns,
            "head_rows": head_rows,
        }
    finally:
        con.close()
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
