"""Trainable v2 — FastAPI Backend"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from auth import BearerTokenAuthMiddleware
from config import settings
from db import engine, init_db
from errors import generic_exception_handler
from observability import init_telemetry
from routers import (
    compare,
    data_explorer,
    experiments,
    files,
    lineage,
    models,
    notebook,
    projects,
    registry,
    s3_browser,
    samples,
    sessions,
    skills as skills_router,
    snapshots,
    stream,
    usage,
)
from services.kernel_manager import kernel_manager
from services.s3_client import get_s3_client

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _init_s3_buckets():
    """Create default S3 buckets if they don't exist."""

    try:
        s3 = get_s3_client()
        for bucket in settings.s3_allowed_buckets:
            try:
                s3.head_bucket(Bucket=bucket)
                logger.info("S3 bucket '%s' exists", bucket)
            except s3.exceptions.ClientError:
                s3.create_bucket(Bucket=bucket)
                logger.info("S3 bucket '%s' created", bucket)
    except Exception as e:
        logger.warning("S3 init skipped (not available): %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    _init_s3_buckets()
    kernel_manager.start_idle_reaper()
    try:
        yield
    finally:
        await kernel_manager.shutdown_all()


app = FastAPI(title="Trainable v2", lifespan=lifespan)
# Init telemetry before middleware/routes so FastAPI auto-instrumentation
# captures every request span. Safe when OTEL_EXPORTER_OTLP_ENDPOINT is unset
# — exporter is a no-op in that case.
init_telemetry(app)
app.add_exception_handler(Exception, generic_exception_handler)

# Opt-in bearer-token auth. No-op when API_AUTH_TOKEN is unset (the default) —
# added before CORSMiddleware so CORS is the outer layer and preflight
# requests are answered before auth runs.
if settings.api_auth_token:
    logger.info("API_AUTH_TOKEN set — bearer-token auth enabled on /api/*")
    app.add_middleware(BearerTokenAuthMiddleware, token=settings.api_auth_token)

# Never pair a wildcard origin with credentials: that combination lets any
# web page script credentialed cross-origin requests against the API. If `*`
# is explicitly configured, honor it but disable credentials.
_cors_wildcard = "*" in settings.cors_origins
if _cors_wildcard:
    logger.warning(
        "CORS_ORIGINS contains '*' — allowing all origins WITHOUT credentials. "
        "List explicit origins to re-enable credentialed requests."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=not _cors_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api")
app.include_router(experiments.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(s3_browser.router, prefix="/api/s3")
app.include_router(files.router, prefix="/api")
app.include_router(data_explorer.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(notebook.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(skills_router.router, prefix="/api")
app.include_router(registry.router, prefix="/api")
app.include_router(compare.router, prefix="/api")
app.include_router(snapshots.router, prefix="/api")
app.include_router(lineage.router, prefix="/api")
app.include_router(samples.router, prefix="/api")


@app.get("/api/health")
async def health():
    """Cheap liveness check — static, no dependencies touched."""
    return {"status": "ok"}


async def _readyz_check_db() -> str:
    try:
        async with engine.connect() as conn:
            # Raw SQL on purpose: cheapest possible round-trip; no ORM model
            # exists (or should) for a connectivity probe.
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as e:
        logger.warning("readyz: database check failed: %s", e)
        return f"error: {e.__class__.__name__}"


async def _readyz_check_s3() -> str:
    try:
        # boto3 is sync — run in a thread so we don't block the event loop.
        # list_buckets is the cheapest call that doesn't assume a bucket exists.
        await asyncio.to_thread(get_s3_client().list_buckets)
        return "ok"
    except Exception as e:
        logger.warning("readyz: s3 check failed: %s", e)
        return f"error: {e.__class__.__name__}"


@app.get("/api/readyz")
async def readyz():
    """Readiness check — pings the DB and S3 concurrently; 503 if either is down."""
    db_status, s3_status = await asyncio.gather(_readyz_check_db(), _readyz_check_s3())
    checks = {"database": db_status, "s3": s3_status}

    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
