"""Standardized error handler for unhandled exceptions."""

import logging

import sentry_sdk
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def generic_exception_handler(request: Request, exc: Exception):
    """Return consistent JSON for any unhandled exception (instead of HTML 500)."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    capture_exception(exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )


def capture_exception(exc: BaseException) -> None:
    """Report an exception to Sentry. No-op when Sentry has no DSN configured
    (`sentry_sdk.capture_exception` is a no-op against an uninitialized SDK),
    and never lets a Sentry-side failure mask the original error.

    Shared by the request-lifecycle handler above and background-task call
    sites (e.g. the agent-runner spawn boundary in routers/sessions.py) so
    errors raised outside a request still reach Sentry.
    """
    try:
        sentry_sdk.capture_exception(exc)
    except Exception:
        logger.debug("[sentry] capture_exception failed", exc_info=True)
