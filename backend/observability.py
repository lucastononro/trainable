"""OpenTelemetry + Sentry + structured-logging setup.

Init order matters: structlog → OTel TracerProvider → instrumentations →
Sentry. Everything no-ops gracefully when the corresponding env var is
unset, so dev environments don't pay a runtime cost.

Hot-path instrumentation lives here too — `agent_span()` and
`sandbox_span()` are thin wrappers exposed for the runner/sandbox modules.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from config import settings

logger = logging.getLogger(__name__)

# Module-level singletons — guarded so re-import in tests doesn't double-init.
_telemetry_initialized = False
_tracer = None


def _truthy(env_value: str | None) -> bool:
    return bool(env_value and env_value.strip())


def _build_resource():
    from opentelemetry.sdk.resources import Resource

    attrs: dict[str, str] = {
        "service.name": settings.otel_service_name,
        "service.version": settings.otel_service_version,
        "deployment.environment": os.getenv("DEPLOY_ENV", "dev"),
    }
    extra = settings.otel_resource_attributes
    if extra:
        for kv in extra.split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                attrs[k.strip()] = v.strip()
    return Resource.create(attrs)


def _init_tracing() -> None:
    """Stand up the TracerProvider + OTLP exporter. Safe to call when
    OTEL_EXPORTER_OTLP_ENDPOINT is unset — we still register a provider so
    `trace.get_tracer_provider()` is non-trivial, but the exporter is a
    no-op (spans don't leave the process)."""
    global _tracer

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError:
        logger.info("[telemetry] opentelemetry not installed — tracing disabled")
        return

    sampler = TraceIdRatioBased(max(0.0, min(1.0, settings.otel_traces_sampler_ratio)))
    provider = TracerProvider(resource=_build_resource(), sampler=sampler)

    endpoint = settings.otel_exporter_otlp_endpoint or os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    if _truthy(endpoint):
        try:
            if (
                (settings.otel_exporter_otlp_protocol or "grpc")
                .lower()
                .startswith("http")
            ):
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
            else:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
            )
            logger.info("[telemetry] OTLP exporter → %s", endpoint)
        except Exception as e:
            logger.warning("[telemetry] OTLP exporter init failed: %s", e)

    # In debug mode, dump every span to stdout. Useful for local hacking
    # without standing up a collector. Off by default — gated on log_level.
    if (settings.log_level or "").upper() == "DEBUG" and not _truthy(endpoint):
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("trainable")


def _init_instrumentations(app) -> None:
    """Auto-instrument FastAPI / SQLAlchemy / httpx / logging."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception as e:
        logger.debug("[telemetry] FastAPI instrumentation skipped: %s", e)

    try:
        from db import engine as _engine
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        # SQLAlchemy's async engine wraps a sync engine — instrument the
        # underlying sync engine.
        SQLAlchemyInstrumentor().instrument(engine=_engine.sync_engine)
    except Exception as e:
        logger.debug("[telemetry] SQLAlchemy instrumentation skipped: %s", e)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        logger.debug("[telemetry] httpx instrumentation skipped: %s", e)

    try:
        # Adds trace_id/span_id fields to every stdlib log record so
        # structlog/JSON formatters can include them.
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(set_logging_format=False)
    except Exception as e:
        logger.debug("[telemetry] logging instrumentation skipped: %s", e)


def _init_sentry() -> None:
    if not _truthy(settings.sentry_dsn):
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment or os.getenv("DEPLOY_ENV", "dev"),
            traces_sample_rate=settings.sentry_traces_sample_rate,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            release=f"{settings.otel_service_name}@{settings.otel_service_version}",
        )
        logger.info("[telemetry] Sentry initialized")
    except ImportError:
        logger.info("[telemetry] sentry-sdk not installed — Sentry disabled")
    except Exception as e:
        logger.warning("[telemetry] Sentry init failed: %s", e)


def _init_structured_logging() -> None:
    """Bridge stdlib logging → structlog with OTel trace context.

    structlog's `processors.merge_contextvars` plus the OTel
    LoggingInstrumentor's stdlib hook means every log record gets
    `otelTraceID` / `otelSpanID` automatically.
    """
    try:
        import structlog
    except ImportError:
        logger.debug("[telemetry] structlog not installed — falling back to stdlib")
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def init_telemetry(app) -> None:
    """Top-level entry point. Idempotent."""
    global _telemetry_initialized
    if _telemetry_initialized:
        return
    _telemetry_initialized = True

    _init_structured_logging()
    _init_tracing()
    _init_instrumentations(app)
    _init_sentry()


# ---------------------------------------------------------------------------
# Hot-path helpers — used by runner/sandbox/usage. Safe to call even when
# init_telemetry() was never invoked (no-op spans).
# ---------------------------------------------------------------------------


def get_tracer():
    """Return the trainable tracer — falls back to no-op when OTel is absent."""
    global _tracer
    if _tracer is not None:
        return _tracer
    try:
        from opentelemetry import trace

        return trace.get_tracer("trainable")
    except ImportError:  # pragma: no cover — defensive

        class _NoopTracer:
            @contextmanager
            def start_as_current_span(self, *_a, **_kw):  # type: ignore[no-untyped-def]
                yield _NoopSpan()

        return _NoopTracer()


class _NoopSpan:
    def set_attribute(self, *_a, **_kw): ...
    def set_attributes(self, *_a, **_kw): ...
    def record_exception(self, *_a, **_kw): ...
    def set_status(self, *_a, **_kw): ...


@contextmanager
def agent_span(
    *,
    agent_type: str,
    session_id: str,
    model: str | None = None,
    depth: int = 0,
    agent_id: str | None = None,
) -> Iterator[object]:
    """Top-level span for one agent run — covers the SDK loop end-to-end."""
    tracer = get_tracer()
    with tracer.start_as_current_span(f"agent.{agent_type}") as span:
        try:
            span.set_attribute("agent.type", agent_type)
            span.set_attribute("agent.session_id", session_id)
            span.set_attribute("agent.depth", depth)
            if model:
                span.set_attribute("agent.model", model)
            if agent_id:
                span.set_attribute("agent.id", agent_id)
        except Exception:
            pass
        yield span


@contextmanager
def sandbox_span(
    *,
    session_id: str,
    stage: str | None,
    gpu: str | None,
    agent_type: str | None,
) -> Iterator[object]:
    """Span around a single Modal sandbox execution."""
    tracer = get_tracer()
    with tracer.start_as_current_span("sandbox.run_code") as span:
        try:
            span.set_attribute("sandbox.session_id", session_id)
            if stage:
                span.set_attribute("sandbox.stage", stage)
            if gpu:
                span.set_attribute("sandbox.gpu", gpu)
            if agent_type:
                span.set_attribute("agent.type", agent_type)
        except Exception:
            pass
        yield span


def record_usage_attributes(
    span,
    *,
    provider: str,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cost_usd: float,
) -> None:
    """Attach usage counters to a span. Safe with the no-op span too."""
    try:
        span.set_attribute("llm.provider", provider)
        if model:
            span.set_attribute("llm.model", model)
        span.set_attribute("llm.usage.input_tokens", int(input_tokens))
        span.set_attribute("llm.usage.output_tokens", int(output_tokens))
        span.set_attribute("llm.usage.cache_read_tokens", int(cache_read))
        span.set_attribute("llm.cost_usd", float(cost_usd))
    except Exception:
        pass


def bind_log_context(**kwargs) -> None:
    """Bind context-vars onto structlog so subsequent log calls inherit them."""
    try:
        import structlog

        structlog.contextvars.bind_contextvars(
            **{k: v for k, v in kwargs.items() if v is not None}
        )
    except Exception:
        pass


def clear_log_context() -> None:
    try:
        import structlog

        structlog.contextvars.clear_contextvars()
    except Exception:
        pass
