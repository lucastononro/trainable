"""PostgreSQL database setup with async SQLAlchemy."""

import logging

from sqlalchemy import event, inspect, text
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


# SQLite doesn't enforce FK constraints unless PRAGMA foreign_keys=ON is set
# per connection. Without this, ON DELETE CASCADE is silently a no-op,
# leaving orphan rows after parent deletes (and breaking our tests). Postgres
# enforces FKs unconditionally so this listener is a SQLite-only concern.
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_fk_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


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

    # Add sandbox_config to projects if missing
    if insp.has_table("projects"):
        columns = [c["name"] for c in insp.get_columns("projects")]
        if "sandbox_config" not in columns:
            connection.execute(
                text("ALTER TABLE projects ADD COLUMN sandbox_config JSON")
            )
            logger.info("[DB] Added sandbox_config column to projects table")

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
    # FK upgrade: usage_events.session_id ON DELETE CASCADE.
    # The original FK was created without a delete action, so deleting a
    # session that has any usage_events rows fails with FK violation —
    # which makes "delete chat" in the UI fail for any session that
    # produced LLM/sandbox events. The model now declares CASCADE so
    # fresh DBs are fine; this block migrates pre-existing Postgres DBs.
    # SQLite doesn't support DROP/ADD CONSTRAINT in-place; we skip it
    # because tests recreate the schema each run via create_all.
    # ------------------------------------------------------------------
    if connection.dialect.name == "postgresql" and insp.has_table("usage_events"):
        try:
            rule = connection.execute(
                text(
                    "SELECT confdeltype FROM pg_constraint "
                    "WHERE conname = 'usage_events_session_id_fkey'"
                )
            ).scalar()
            # confdeltype: 'a'=NO ACTION, 'r'=RESTRICT, 'c'=CASCADE,
            # 'n'=SET NULL, 'd'=SET DEFAULT. Migrate anything that's not CASCADE.
            if rule and rule != "c":
                connection.execute(
                    text(
                        "ALTER TABLE usage_events "
                        "DROP CONSTRAINT usage_events_session_id_fkey"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE usage_events "
                        "ADD CONSTRAINT usage_events_session_id_fkey "
                        "FOREIGN KEY (session_id) REFERENCES sessions(id) "
                        "ON DELETE CASCADE"
                    )
                )
                logger.info(
                    "[DB] Migrated usage_events.session_id FK to ON DELETE CASCADE"
                )
        except Exception as e:
            logger.warning("[DB] usage_events FK migration skipped: %s", e)

    # ------------------------------------------------------------------
    # Lineage feature: agent-declared experiments + dataset versions.
    # New columns on existing tables; new tables created via create_all.
    # ------------------------------------------------------------------
    if insp.has_table("experiments"):
        ecols = [c["name"] for c in insp.get_columns("experiments")]
        for col_name, col_def in [
            ("session_id", "VARCHAR(36)"),
            ("hypothesis", "TEXT"),
            ("started_at", "VARCHAR"),
            ("completed_at", "VARCHAR"),
            ("tags", "JSON"),
        ]:
            if col_name not in ecols:
                connection.execute(
                    text(f"ALTER TABLE experiments ADD COLUMN {col_name} {col_def}")
                )
                logger.info("[DB] Added %s column to experiments", col_name)
        if "state" not in ecols:
            # Legacy experiments default to 'trained' so they don't show
            # as in-progress in the new sidebar.
            connection.execute(
                text(
                    "ALTER TABLE experiments ADD COLUMN state VARCHAR(20)"
                    " DEFAULT 'trained'"
                )
            )
            logger.info("[DB] Added state column to experiments")
        if "pinned" not in ecols:
            connection.execute(
                text("ALTER TABLE experiments ADD COLUMN pinned BOOLEAN DEFAULT FALSE")
            )
            logger.info("[DB] Added pinned column to experiments")
        if "archived" not in ecols:
            connection.execute(
                text(
                    "ALTER TABLE experiments ADD COLUMN archived BOOLEAN DEFAULT FALSE"
                )
            )
            logger.info("[DB] Added archived column to experiments")
        # Backfill: each legacy experiment maps to its earliest session.
        connection.execute(
            text(
                "UPDATE experiments SET session_id = ("
                " SELECT sessions.id FROM sessions"
                " WHERE sessions.experiment_id = experiments.id"
                " ORDER BY sessions.created_at ASC LIMIT 1"
                ") WHERE session_id IS NULL"
            )
        )
        # Loosen experiments.dataset_ref NOT NULL on Postgres so new
        # agent-declared experiments can omit it.
        if connection.dialect.name == "postgresql":
            try:
                connection.execute(
                    text(
                        "ALTER TABLE experiments ALTER COLUMN dataset_ref DROP NOT NULL"
                    )
                )
            except Exception as e:
                logger.debug("[DB] dataset_ref nullability already loosened: %s", e)

    if insp.has_table("sessions"):
        scols = [c["name"] for c in insp.get_columns("sessions")]
        if "project_id" not in scols:
            connection.execute(
                text("ALTER TABLE sessions ADD COLUMN project_id VARCHAR(36)")
            )
            logger.info("[DB] Added project_id column to sessions")
        if "name" not in scols:
            connection.execute(
                text("ALTER TABLE sessions ADD COLUMN name VARCHAR(255)")
            )
            logger.info("[DB] Added name column to sessions")
        # Backfill: legacy session.experiment_id → experiments.project_id.
        connection.execute(
            text(
                "UPDATE sessions SET project_id = ("
                " SELECT experiments.project_id FROM experiments"
                " WHERE experiments.id = sessions.experiment_id"
                ") WHERE project_id IS NULL AND experiment_id IS NOT NULL"
            )
        )
        # Loosen experiment_id NOT NULL on Postgres so agent-driven
        # sessions don't have to fake a parent at create time.
        if connection.dialect.name == "postgresql":
            try:
                connection.execute(
                    text(
                        "ALTER TABLE sessions ALTER COLUMN experiment_id DROP NOT NULL"
                    )
                )
            except Exception as e:
                logger.debug(
                    "[DB] sessions.experiment_id nullability already loosened: %s", e
                )

    if insp.has_table("registered_models"):
        mcols = [c["name"] for c in insp.get_columns("registered_models")]
        # Loosen source_session_id NOT NULL on Postgres so the new
        # agent-declared register-model path can write rows when the
        # experiment was created via the legacy upload route (which sets
        # Session.experiment_id, not Experiment.session_id, leaving the
        # session-back-reference indirect). Models can resolve the
        # session via the experiment regardless.
        if connection.dialect.name == "postgresql":
            try:
                connection.execute(
                    text(
                        "ALTER TABLE registered_models "
                        "ALTER COLUMN source_session_id DROP NOT NULL"
                    )
                )
            except Exception as e:
                logger.debug(
                    "[DB] registered_models.source_session_id nullability already loosened: %s",
                    e,
                )
        for col_name, col_def in [
            ("experiment_id", "VARCHAR(36)"),
            ("description", "TEXT"),
            ("hyperparams", "JSON"),
            ("dataset_refs", "JSON"),
            ("metrics_history", "JSON"),
            ("serving_app_path", "VARCHAR(512)"),
        ]:
            if col_name not in mcols:
                connection.execute(
                    text(
                        f"ALTER TABLE registered_models ADD COLUMN {col_name} {col_def}"
                    )
                )
                logger.info("[DB] Added %s column to registered_models", col_name)
        # Backfill experiment_id from the legacy session→experiment join.
        connection.execute(
            text(
                "UPDATE registered_models SET experiment_id = ("
                " SELECT sessions.experiment_id FROM sessions"
                " WHERE sessions.id = registered_models.source_session_id"
                ") WHERE experiment_id IS NULL AND source_session_id IS NOT NULL"
            )
        )

    if insp.has_table("dataset_versions"):
        dcols = [c["name"] for c in insp.get_columns("dataset_versions")]
        if "kind" not in dcols:
            connection.execute(
                text(
                    "ALTER TABLE dataset_versions ADD COLUMN kind VARCHAR(20)"
                    " NOT NULL DEFAULT 'raw'"
                )
            )
            logger.info("[DB] Added kind column to dataset_versions")
        for col_name, col_def in [
            ("name", "VARCHAR(255)"),
            ("description", "TEXT"),
            ("parent_id", "INTEGER"),
            ("source_session_id", "VARCHAR(36)"),
            ("source_experiment_id", "VARCHAR(36)"),
            ("metadata", "JSON"),
        ]:
            if col_name not in dcols:
                connection.execute(
                    text(
                        f"ALTER TABLE dataset_versions ADD COLUMN {col_name} {col_def}"
                    )
                )
                logger.info("[DB] Added %s column to dataset_versions", col_name)

    if insp.has_table("run_snapshots"):
        rcols = [c["name"] for c in insp.get_columns("run_snapshots")]
        if "experiment_id" not in rcols:
            connection.execute(
                text("ALTER TABLE run_snapshots ADD COLUMN experiment_id VARCHAR(36)")
            )
            logger.info("[DB] Added experiment_id column to run_snapshots")
        connection.execute(
            text(
                "UPDATE run_snapshots SET experiment_id = ("
                " SELECT sessions.experiment_id FROM sessions"
                " WHERE sessions.id = run_snapshots.session_id"
                ") WHERE experiment_id IS NULL AND session_id IS NOT NULL"
            )
        )
        if connection.dialect.name == "postgresql":
            try:
                connection.execute(
                    text(
                        "ALTER TABLE run_snapshots DROP CONSTRAINT IF EXISTS"
                        " run_snapshots_session_id_key"
                    )
                )
            except Exception as e:
                logger.debug("[DB] run_snapshots session_id unique drop skipped: %s", e)

    # ------------------------------------------------------------------
    # Indexes on hot FK columns. `CREATE INDEX IF NOT EXISTS` is supported
    # by both Postgres and SQLite, so this is a no-op on fresh DBs where
    # create_all already built them, and fills the gap on DBs upgraded
    # from the pre-index schema.
    # ------------------------------------------------------------------
    indexes = [
        ("ix_experiments_project_id", "experiments", "project_id"),
        ("ix_experiments_session_id", "experiments", "session_id"),
        ("ix_experiments_state", "experiments", "state"),
        ("ix_sessions_experiment_id", "sessions", "experiment_id"),
        ("ix_sessions_project_id", "sessions", "project_id"),
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
        ("ix_usage_events_session_id", "usage_events", "session_id"),
        ("ix_usage_events_project_id", "usage_events", "project_id"),
        ("ix_registered_models_project_id", "registered_models", "project_id"),
        ("ix_registered_models_experiment_id", "registered_models", "experiment_id"),
        (
            "ix_registered_models_source_session",
            "registered_models",
            "source_session_id",
        ),
        ("ix_deployments_model_id", "deployments", "model_id"),
        ("ix_run_snapshots_session_id", "run_snapshots", "session_id"),
        ("ix_run_snapshots_experiment_id", "run_snapshots", "experiment_id"),
        ("ix_dataset_versions_project_id", "dataset_versions", "project_id"),
        ("ix_dataset_versions_hash", "dataset_versions", "hash"),
        ("ix_dataset_versions_kind", "dataset_versions", "kind"),
        ("ix_dataset_versions_parent_id", "dataset_versions", "parent_id"),
        (
            "ix_dataset_versions_source_experiment",
            "dataset_versions",
            "source_experiment_id",
        ),
        (
            "ix_experiment_datasets_experiment_id",
            "experiment_datasets",
            "experiment_id",
        ),
        (
            "ix_experiment_datasets_dataset_version_id",
            "experiment_datasets",
            "dataset_version_id",
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
        DatasetVersion,
        Deployment,
        Experiment,
        ExperimentDataset,
        Message,
        Metric,
        ProcessedDatasetMeta,
        Project,
        RegisteredModel,
        RunSnapshot,
        Session,
        UsageEvent,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_migrations)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
