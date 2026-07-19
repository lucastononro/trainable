"""Tests for db._run_migrations — the ALTER-TABLE upgrade path for
existing databases (create_all covers fresh DBs; this covers upgrades)."""

import os
import sqlite3
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, text

from db import _run_migrations


@pytest.fixture
def legacy_engine():
    """A file-backed sqlite DB shaped like a pre-multi-provider install:
    deployments exists WITHOUT provider / provider_endpoint_id."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    raw = sqlite3.connect(path)
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
    engine = create_engine(f"sqlite:///{path}")
    yield engine
    engine.dispose()
    os.unlink(path)


class TestDeploymentProviderMigration:
    def test_adds_provider_columns(self, legacy_engine):
        with legacy_engine.begin() as conn:
            _run_migrations(conn)
        with legacy_engine.connect() as conn:
            cols = [c["name"] for c in inspect(conn).get_columns("deployments")]
            assert "provider" in cols
            assert "provider_endpoint_id" in cols

    def test_existing_rows_default_to_modal(self, legacy_engine):
        with legacy_engine.begin() as conn:
            _run_migrations(conn)
        with legacy_engine.connect() as conn:
            provider, endpoint_id = conn.execute(
                text(
                    "SELECT provider, provider_endpoint_id "
                    "FROM deployments WHERE id='d1'"
                )
            ).one()
            assert provider == "modal"
            assert endpoint_id is None

    def test_idempotent(self, legacy_engine):
        with legacy_engine.begin() as conn:
            _run_migrations(conn)
        with legacy_engine.begin() as conn:
            _run_migrations(conn)  # second run must not raise
        with legacy_engine.connect() as conn:
            cols = [c["name"] for c in inspect(conn).get_columns("deployments")]
            assert cols.count("provider") == 1
