"""Centralized configuration — single source of truth for all settings.

All values can be overridden via environment variables or a .env file.
Variable names match the field names in UPPER_CASE (e.g. SANDBOX_TIMEOUT=300).
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # -- Database --
    database_url: str = "sqlite+aiosqlite:///trainable.db"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle: int = 3600

    # -- S3 / MinIO --
    s3_endpoint: str = "http://localhost:4566"
    s3_endpoint_external: Optional[str] = None  # falls back to s3_endpoint
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_region: str = "us-east-1"

    # -- Modal --
    modal_app_name: str = "trainable"
    modal_volume_name: str = "trainable-data"

    # -- Claude / Agent --
    claude_model: str = "claude-sonnet-4-6"
    claude_code_oauth_token: str = ""
    agent_max_turns: int = 30
    agent_timeout_seconds: int = Field(
        default=1800,
        description="Overall wall-clock timeout for an agent run (seconds)",
    )
    agent_abort_timeout: float = 5.0

    # -- Sandbox --
    # Per-execution timeout for code running in a Modal sandbox. This is the
    # single timeout that governs an agent's tool calls — when it fires, the
    # sandbox is killed and the runner returns a tool_result describing the
    # timeout so the model can adapt (smaller chunk, different approach) or
    # stop. Override per project via the `default`/`training` sandbox
    # profiles' `timeout` field.
    sandbox_timeout: int = Field(
        default=600, description="Per-execution timeout in Modal sandbox (seconds)"
    )

    # -- SSE / Broadcaster --
    sse_keepalive_seconds: float = 30.0
    broadcaster_max_queue_size: int = 1000

    # -- CORS --
    cors_origins: list[str] = ["*"]

    # -- Upload limits --
    max_upload_size_bytes: int = 500 * 1024 * 1024  # 500 MB

    # -- Data explorer --
    query_default_limit: int = 100
    query_max_limit: int = 1000
    preview_default_limit: int = 50

    # -- Logging --
    log_level: str = "INFO"

    # -- Observability (OpenTelemetry + Sentry) --
    # All optional. When unset, telemetry init is a no-op.
    otel_service_name: str = "trainable-backend"
    otel_service_version: str = "0.1.0"
    otel_exporter_otlp_endpoint: Optional[str] = None
    # "grpc" (default OTLP/gRPC port 4317) or "http/protobuf" (port 4318)
    otel_exporter_otlp_protocol: str = "grpc"
    otel_resource_attributes: Optional[str] = None  # comma-separated k=v pairs
    otel_traces_sampler_ratio: float = 1.0  # 0.0 to 1.0 — fraction sampled

    sentry_dsn: Optional[str] = None
    sentry_environment: Optional[str] = None
    sentry_traces_sample_rate: float = 0.1


settings = Settings()
