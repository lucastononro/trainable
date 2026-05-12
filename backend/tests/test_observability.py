"""Telemetry init must be safe with or without OTel env vars."""

from __future__ import annotations


def test_get_tracer_works_without_init():
    """Calling get_tracer() before init_telemetry() returns a usable tracer."""
    from observability import get_tracer

    tracer = get_tracer()
    with tracer.start_as_current_span("preinit-span") as span:
        span.set_attribute("k", "v")  # must not raise


def test_agent_span_attributes():
    from observability import agent_span

    with agent_span(
        agent_type="eda",
        session_id="sess-123",
        model="claude-sonnet-4-6",
        depth=0,
        agent_id="root",
    ) as span:
        span.set_attribute("custom", 1)


def test_sandbox_span_optional_attrs():
    from observability import sandbox_span

    # gpu / agent_type / stage all optional — none should crash.
    with sandbox_span(session_id="s", stage=None, gpu=None, agent_type=None) as span:
        span.set_attribute("ok", True)


def test_record_usage_attributes_noop_safe():
    """Should never raise even with weird inputs."""
    from observability import _NoopSpan, record_usage_attributes

    record_usage_attributes(
        _NoopSpan(),
        provider="claude",
        model=None,
        input_tokens=0,
        output_tokens=0,
        cache_read=0,
        cost_usd=0.0,
    )


def test_init_telemetry_idempotent(monkeypatch):
    """Calling init_telemetry twice must be safe (test harness reuses the app)."""
    from fastapi import FastAPI

    import observability

    # Force a re-init in this test by clearing the guard
    monkeypatch.setattr(observability, "_telemetry_initialized", False)
    app = FastAPI()
    observability.init_telemetry(app)
    observability.init_telemetry(app)  # second call must no-op


def test_init_with_otlp_endpoint(monkeypatch):
    """Setting OTEL_EXPORTER_OTLP_ENDPOINT shouldn't crash even if no collector."""
    from fastapi import FastAPI

    import observability

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    monkeypatch.setattr(observability, "_telemetry_initialized", False)
    monkeypatch.setattr(
        observability.settings, "otel_exporter_otlp_endpoint", "http://localhost:4317"
    )
    app = FastAPI()
    observability.init_telemetry(app)


def test_bind_log_context_safe():
    from observability import bind_log_context, clear_log_context

    bind_log_context(session_id="abc", agent_type="eda")
    clear_log_context()
