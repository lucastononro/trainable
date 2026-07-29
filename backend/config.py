"""Centralized configuration — single source of truth for all settings.

All values can be overridden via environment variables or a .env file.
Variable names match the field names in UPPER_CASE (e.g. SANDBOX_TIMEOUT=300).
"""

import json
from typing import Annotated, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


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
        description=(
            "Wall-clock timeout for a single provider LLM call (seconds). "
            "Enforced inside each provider around the HTTP request only, "
            "so tool-execution time is never counted."
        ),
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

    # -- API auth --
    # Opt-in bearer-token auth (env: API_AUTH_TOKEN). When unset (default),
    # every endpoint is open — unchanged behavior. When set, /api/* requires
    # `Authorization: Bearer <token>` (health/readyz exempt; the SSE stream
    # endpoint also accepts ?token= since EventSource can't send headers).
    api_auth_token: Optional[str] = None

    # -- CORS --
    # Allowed browser origins (env: CORS_ORIGINS, comma-separated, e.g.
    # `CORS_ORIGINS=https://app.example.com,http://localhost:3000`; a JSON
    # array is also accepted). Defaults to the local frontend. `*` alone is
    # honored but never combined with credentials (see main.py); mixing `*`
    # with explicit origins is rejected at startup.
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, v):
        """Accept a comma-separated string (env var), a JSON array, or a list.

        `NoDecode` hands us the raw env string, so the pre-NoDecode JSON-array
        format (`CORS_ORIGINS=["http://..."]`) would otherwise be comma-split
        into garbage like `['["http://..."]']` — parse it explicitly instead.
        """
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                try:
                    decoded = json.loads(stripped)
                except ValueError as exc:
                    raise ValueError(
                        "CORS_ORIGINS looks like a JSON array but is not valid "
                        "JSON. Use a comma-separated list instead, e.g. "
                        "CORS_ORIGINS=https://app.example.com,http://localhost:3000"
                    ) from exc
                if not isinstance(decoded, list) or not all(
                    isinstance(o, str) for o in decoded
                ):
                    raise ValueError(
                        "CORS_ORIGINS JSON value must be an array of strings."
                    )
                origins = [o.strip() for o in decoded if o.strip()]
            else:
                origins = [o.strip() for o in stripped.split(",") if o.strip()]
        else:
            origins = v
        # Reject `*` mixed with explicit origins: main.py disables credentials
        # whenever `*` is present, which would silently strip credentials from
        # the explicit entries too. Fail fast with a clear message instead.
        if (
            isinstance(origins, list)
            and "*" in origins
            and any(o != "*" for o in origins)
        ):
            raise ValueError(
                "CORS_ORIGINS: cannot mix '*' with explicit origins — list only "
                "explicit origins to enable credentialed requests, or use '*' "
                "alone (credentials will be disabled)."
            )
        return origins

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
