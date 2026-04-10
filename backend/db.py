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


async def init_db():
    from models import (  # noqa: F401
        Artifact,
        Experiment,
        Message,
        Metric,
        ProcessedDatasetMeta,
        Session,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_migrations)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
