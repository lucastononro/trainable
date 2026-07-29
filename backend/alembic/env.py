"""Alembic environment script.

Wires Alembic to the app's own settings/models instead of a second,
hand-maintained config:

  - the DB URL always comes from ``config.settings.database_url`` (so
    DATABASE_URL / .env stays the single source of truth — nothing new to
    keep in sync in alembic.ini),
  - ``target_metadata`` is ``db.Base.metadata`` after importing every model
    module, so ``alembic revision --autogenerate`` sees the full schema.

The app uses an async engine (asyncpg / aiosqlite), so migrations run
through SQLAlchemy's async engine too, following the pattern from the
Alembic cookbook for async applications:
https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import the app's settings + models so Base.metadata is fully populated
# and the DB URL matches exactly what the running app uses.
from config import settings
from db import Base
import models  # noqa: F401  (populates Base.metadata as a side effect)

# this is the Alembic Config object, which provides access to the values
# within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
#
# disable_existing_loggers=False is load-bearing: this env.py also runs
# in-process at app startup (init_db() -> _run_alembic_sync), after main.py
# and every module-level `logging.getLogger(__name__)` have already been
# created. fileConfig's default (True) would silently disable all of those
# app loggers the moment migrations run — killing app logging after boot
# (and breaking any caplog-based test that runs after an Alembic test).
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The app's own metadata — autogenerate diffs against this.
target_metadata = Base.metadata

# Always drive the URL from the app's settings, not alembic.ini.
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL, no DB connection)."""
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Batch mode so future ALTER-style migrations work on SQLite (which
        # cannot ALTER COLUMN / DROP COLUMN natively); a no-op on Postgres.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Batch mode so future ALTER-style migrations work on SQLite (which
        # cannot ALTER COLUMN / DROP COLUMN natively); a no-op on Postgres.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against the async engine built from settings."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Called both from the `alembic` CLI (its own fresh event loop) and
    programmatically from `db.py` via `asyncio.to_thread` (also a fresh
    thread with no running loop) — `asyncio.run()` is safe in both cases.
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
