"""Tests for the deployments provider-columns Alembic revision (PR #145).

Fresh DBs get `provider` / `provider_endpoint_id` from a plain
`alembic upgrade head`; legacy (pre-Alembic) DBs are stamped at the
initial revision and then upgraded (see `db._run_alembic_sync`), so the
ALTERs reach them too — with existing rows backfilled to provider='modal'.
Covers both paths plus idempotency.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text

from db import _LEGACY_SCHEMA_MARKER_TABLES, _run_alembic_sync


@pytest.fixture()
def _sqlite_file_db(tmp_path, monkeypatch):
    """Point settings.database_url at a fresh file-backed SQLite DB (same
    redirect mechanism as tests/test_alembic_migrations.py)."""
    from config import settings

    db_path = tmp_path / "deployments_provider_test.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    return db_path


def _deployment_cols(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        return [c["name"] for c in inspect(conn).get_columns("deployments")]


def _create_legacy_db(db_path):
    """Shape a pre-multi-provider install: the full legacy marker set plus
    a deployments table WITHOUT provider / provider_endpoint_id, holding
    one live Modal-era row."""
    raw = sqlite3.connect(db_path)
    for name in _LEGACY_SCHEMA_MARKER_TABLES:
        raw.execute(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)')
    raw.execute(
        """CREATE TABLE deployments (
            id VARCHAR(36) PRIMARY KEY,
            model_id VARCHAR(36),
            endpoint_url VARCHAR(512),
            status VARCHAR(20),
            error TEXT,
            modal_app VARCHAR(255),
            modal_function VARCHAR(255),
            compute VARCHAR(20),
            created_at VARCHAR,
            updated_at VARCHAR
        )"""
    )
    raw.execute(
        "INSERT INTO deployments (id, model_id, status) VALUES ('d1', 'm1', 'live')"
    )
    raw.commit()
    raw.close()


class TestDeploymentProviderMigration:
    def test_fresh_upgrade_head_adds_provider_columns(self, _sqlite_file_db):
        # Sync on purpose: _run_alembic_sync drives its own event loop.
        _run_alembic_sync(stamp_only=False)

        cols = _deployment_cols(_sqlite_file_db)
        assert "provider" in cols
        assert "provider_endpoint_id" in cols

    def test_legacy_stamp_then_upgrade_adds_columns(self, _sqlite_file_db):
        _create_legacy_db(_sqlite_file_db)
        _run_alembic_sync(stamp_only=True)

        cols = _deployment_cols(_sqlite_file_db)
        assert "provider" in cols
        assert "provider_endpoint_id" in cols

    def test_legacy_rows_backfill_to_modal(self, _sqlite_file_db):
        _create_legacy_db(_sqlite_file_db)
        _run_alembic_sync(stamp_only=True)

        engine = create_engine(f"sqlite:///{_sqlite_file_db}")
        with engine.connect() as conn:
            provider, endpoint_id = conn.execute(
                text(
                    "SELECT provider, provider_endpoint_id "
                    "FROM deployments WHERE id='d1'"
                )
            ).one()
        assert provider == "modal"
        assert endpoint_id is None

    def test_upgrade_is_idempotent(self, _sqlite_file_db):
        _create_legacy_db(_sqlite_file_db)
        _run_alembic_sync(stamp_only=True)
        _run_alembic_sync(stamp_only=False)  # second boot: plain upgrade, no-op

        cols = _deployment_cols(_sqlite_file_db)
        assert cols.count("provider") == 1
