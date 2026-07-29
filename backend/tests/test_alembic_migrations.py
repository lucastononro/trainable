"""Tests for the Alembic boot-time migration path in db.py.

Covers the stamp-vs-upgrade heuristic (`_pre_alembic_schema_present`) and
schema-sanity checks that `alembic upgrade head` / `alembic stamp head` on a
fresh SQLite DB behave as init_db() expects (regression guard for PR #121 and
the review finding on #153).

These are single-connection tests; the concurrent multi-instance boot race is
intentionally NOT simulated here — boot-time migration assumes one app
instance (see the NOTE in `init_db()` and backend/AGENTS.md, "Database").
"""

import pytest
from sqlalchemy import create_engine, inspect, text

from db import (
    _INITIAL_ALEMBIC_REVISION,
    _LEGACY_SCHEMA_MARKER_TABLES,
    _pre_alembic_schema_present,
    _run_alembic_sync,
)


def _make_sync_sqlite_engine():
    return create_engine("sqlite://")


def _create_tables(conn, names):
    for name in names:
        conn.execute(text(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)'))


def test_heuristic_empty_db_takes_upgrade_path():
    engine = _make_sync_sqlite_engine()
    with engine.begin() as conn:
        assert _pre_alembic_schema_present(conn) is False


def test_heuristic_full_legacy_schema_takes_stamp_path():
    engine = _make_sync_sqlite_engine()
    with engine.begin() as conn:
        _create_tables(conn, _LEGACY_SCHEMA_MARKER_TABLES)
        assert _pre_alembic_schema_present(conn) is True


def test_heuristic_already_stamped_db_takes_upgrade_path():
    engine = _make_sync_sqlite_engine()
    with engine.begin() as conn:
        _create_tables(conn, (*_LEGACY_SCHEMA_MARKER_TABLES, "alembic_version"))
        assert _pre_alembic_schema_present(conn) is False


def test_heuristic_partial_legacy_schema_falls_through_to_upgrade():
    # A DB with only *some* marker tables must NOT be stamped — upgrade head
    # should run and surface a loud DDL error rather than silently stamping
    # an incomplete schema (see the comment on _LEGACY_SCHEMA_MARKER_TABLES).
    engine = _make_sync_sqlite_engine()
    with engine.begin() as conn:
        _create_tables(conn, _LEGACY_SCHEMA_MARKER_TABLES[:2])
        assert _pre_alembic_schema_present(conn) is False


@pytest.fixture()
def _sqlite_file_db(tmp_path, monkeypatch):
    """Point settings.database_url at a fresh file-backed SQLite DB.

    Both db._alembic_config() and alembic/env.py read
    config.settings.database_url at call time, so patching the attribute is
    enough to redirect the whole Alembic run.
    """
    from config import settings

    db_path = tmp_path / "alembic_test.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    return db_path


def test_upgrade_head_on_fresh_db_creates_full_schema(_sqlite_file_db):
    # Sync test on purpose: _run_alembic_sync runs asyncio.run() internally
    # (via alembic/env.py) and must be called with no running event loop.
    _run_alembic_sync(stamp_only=False)

    engine = create_engine(f"sqlite:///{_sqlite_file_db}")
    with engine.connect() as conn:
        insp = inspect(conn)
        for table in _LEGACY_SCHEMA_MARKER_TABLES:
            assert insp.has_table(table), f"upgrade head did not create {table!r}"
        assert insp.has_table("alembic_version")
        # Next boot must take the upgrade path (a no-op at head), not stamp.
        assert _pre_alembic_schema_present(conn) is False


def test_stamp_marks_legacy_db_then_upgrades_to_head(_sqlite_file_db):
    # Simulate the legacy-deployment boot: schema exists (markers suffice for
    # the stamp command itself), no alembic_version yet.
    engine = create_engine(f"sqlite:///{_sqlite_file_db}")
    with engine.begin() as conn:
        _create_tables(conn, _LEGACY_SCHEMA_MARKER_TABLES)

    _run_alembic_sync(stamp_only=True)

    with engine.connect() as conn:
        insp = inspect(conn)
        assert insp.has_table("alembic_version")
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version, "stamp+upgrade left alembic_version empty"
        # The stamp lands on the initial revision and the subsequent upgrade
        # applies post-cutover DDL (e.g. projects.training_config, PR #164) —
        # stamping straight at head would silently skip it.
        assert version != _INITIAL_ALEMBIC_REVISION
        project_cols = [c["name"] for c in insp.get_columns("projects")]
        assert "training_config" in project_cols
        # stamp+upgrade must not create any migration-managed tables beyond
        # the markers we made + alembic_version itself.
        assert set(insp.get_table_names()) == {
            *_LEGACY_SCHEMA_MARKER_TABLES,
            "alembic_version",
        }
        assert _pre_alembic_schema_present(conn) is False


def test_upgrade_head_on_fresh_db_has_training_config_column(_sqlite_file_db):
    _run_alembic_sync(stamp_only=False)

    engine = create_engine(f"sqlite:///{_sqlite_file_db}")
    with engine.connect() as conn:
        project_cols = [c["name"] for c in inspect(conn).get_columns("projects")]
        assert "training_config" in project_cols
