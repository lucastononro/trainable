"""Trainable v2 — FastAPI Backend"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db import init_db
from errors import generic_exception_handler
from routers import data_explorer, experiments, files, models, s3_browser, sessions, stream
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
        for bucket in ["datasets", "experiments"]:
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
    yield


app = FastAPI(title="Trainable v2", lifespan=lifespan)
app.add_exception_handler(Exception, generic_exception_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(experiments.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(s3_browser.router, prefix="/api/s3")
app.include_router(files.router, prefix="/api")
app.include_router(data_explorer.router, prefix="/api")
app.include_router(models.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
