"""PostgreSQL database setup with async SQLAlchemy."""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

logger = logging.getLogger(__name__)

_engine_kwargs: dict = {"echo": False}
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
    )

engine = create_async_engine(settings.database_url, **_engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _run_migrations(connection):
    """Add columns/tables that create_all won't add to existing tables."""
    insp = inspect(connection)

    # Add s3_path to artifacts if missing
    if insp.has_table("artifacts"):
        columns = [c["name"] for c in insp.get_columns("artifacts")]
        if "s3_path" not in columns:
            connection.execute(
                text("ALTER TABLE artifacts ADD COLUMN s3_path VARCHAR(512)")
            )
            logger.info("[DB] Added s3_path column to artifacts table")

    # Add model to sessions if missing
    if insp.has_table("sessions"):
        columns = [c["name"] for c in insp.get_columns("sessions")]
        if "model" not in columns:
            connection.execute(
                text("ALTER TABLE sessions ADD COLUMN model VARCHAR(100)")
            )
            logger.info("[DB] Added model column to sessions table")

    # Add stage and run_tag to metrics if missing
    if insp.has_table("metrics"):
        columns = [c["name"] for c in insp.get_columns("metrics")]
        if "stage" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE metrics ADD COLUMN stage VARCHAR(50) NOT NULL DEFAULT 'train'"
                )
            )
            logger.info("[DB] Added stage column to metrics table")
        if "run_tag" not in columns:
            connection.execute(
                text("ALTER TABLE metrics ADD COLUMN run_tag VARCHAR(100)")
            )
            logger.info("[DB] Added run_tag column to metrics table")

    # ------------------------------------------------------------------
    # Phase A — projects foundation
    # ------------------------------------------------------------------
    # 1. projects table is created by Base.metadata.create_all before this runs.

    # 2. Add project_id / updated_at to experiments if missing
    if insp.has_table("experiments"):
        ecols = [c["name"] for c in insp.get_columns("experiments")]
        if "project_id" not in ecols:
            connection.execute(
                text("ALTER TABLE experiments ADD COLUMN project_id VARCHAR(36)")
            )
            logger.info("[DB] Added project_id column to experiments")
        if "updated_at" not in ecols:
            connection.execute(
                text("ALTER TABLE experiments ADD COLUMN updated_at VARCHAR")
            )
            logger.info("[DB] Added updated_at column to experiments")

        # 3. Destructive wipe of pre-migration experiments (user decision).
        #    Anything still NULL means it predates the projects schema.
        orphans = (
            connection.execute(
                text("SELECT COUNT(*) FROM experiments WHERE project_id IS NULL")
            ).scalar()
            or 0
        )
        if orphans > 0:
            logger.info(
                "[DB] Wiping %d pre-migration experiments and their children",
                orphans,
            )
            # Delete leaves first so FK constraints don't trip.
            orphan_sessions_subq = (
                "SELECT id FROM sessions WHERE experiment_id IN "
                "(SELECT id FROM experiments WHERE project_id IS NULL)"
            )
            if insp.has_table("messages"):
                connection.execute(
                    text(
                        f"DELETE FROM messages WHERE session_id IN ({orphan_sessions_subq})"
                    )
                )
            if insp.has_table("artifacts"):
                connection.execute(
                    text(
                        f"DELETE FROM artifacts WHERE session_id IN ({orphan_sessions_subq})"
                    )
                )
            if insp.has_table("metrics"):
                connection.execute(
                    text(
                        f"DELETE FROM metrics WHERE session_id IN ({orphan_sessions_subq})"
                    )
                )
            if insp.has_table("processed_dataset_meta"):
                connection.execute(
                    text(
                        "DELETE FROM processed_dataset_meta WHERE session_id IN "
                        f"({orphan_sessions_subq})"
                    )
                )
            connection.execute(
                text(
                    "DELETE FROM sessions WHERE experiment_id IN "
                    "(SELECT id FROM experiments WHERE project_id IS NULL)"
                )
            )
            connection.execute(text("DELETE FROM experiments WHERE project_id IS NULL"))
            logger.info("[DB] Wiped %d experiments", orphans)

    # ------------------------------------------------------------------
    # Indexes on hot FK columns. `CREATE INDEX IF NOT EXISTS` is supported
    # by both Postgres and SQLite, so this is a no-op on fresh DBs where
    # create_all already built them, and fills the gap on DBs upgraded
    # from the pre-index schema.
    # ------------------------------------------------------------------
    indexes = [
        ("ix_experiments_project_id", "experiments", "project_id"),
        ("ix_sessions_experiment_id", "sessions", "experiment_id"),
        ("ix_messages_session_id", "messages", "session_id"),
        ("ix_artifacts_session_id", "artifacts", "session_id"),
        ("ix_metrics_session_id", "metrics", "session_id"),
        (
            "ix_processed_dataset_meta_session_id",
            "processed_dataset_meta",
            "session_id",
        ),
        (
            "ix_processed_dataset_meta_experiment_id",
            "processed_dataset_meta",
            "experiment_id",
        ),
    ]
    for idx_name, table, column in indexes:
        if insp.has_table(table):
            try:
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})")
                )
            except Exception as e:
                logger.debug("Index %s create skipped: %s", idx_name, e)


async def init_db():
    from models import (  # noqa: F401
        Artifact,
        Experiment,
        Message,
        Metric,
        ProcessedDatasetMeta,
        Project,
        Session,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_migrations)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
