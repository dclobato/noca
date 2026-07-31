#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Animator module settings.

Reads the same ``NOCA_``-prefixed ``.env`` file as the other runtime modules,
but only the subset the animator needs: PostgreSQL and Valkey connectivity plus
its own ``NOCA_ANIMATOR_*`` knobs. The animator reads the shared schema directly
through SQLAlchemy Core; it never imports the Web declarative base.

Animator-specific fields declare an explicit ``validation_alias`` so they resolve
to ``NOCA_ANIMATOR_*`` (the alias replaces the ``env_prefix``, avoiding an
accidental ``NOCA_NOCA_ANIMATOR_*`` double prefix).
"""

import logging
from ipaddress import ip_address, ip_network

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment


class Settings(BaseSettings):
    """Animator runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="NOCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DB_USER: str
    DB_PASSWORD: str
    DB_SERVER: str
    DB_PORT: int = Field(default=5432, gt=0, le=65535, description="PostgreSQL port (1-65535)")
    DB_NAME: str

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    ENVIRONMENT: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Runtime environment ('development' or 'production')",
    )
    LOG_LEVEL: str | None = Field(
        default=None,
        description=(
            "Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). "
            "When unset, falls back to DEBUG in development and INFO in production."
        ),
    )
    BRAND_NAME: str = Field(
        default="NOCA Animator",
        validation_alias="NOCA_ANIMATOR_BRAND_NAME",
        description="Brand name shown on animator pages.",
    )
    HEALTHMON_URL: str = Field(
        default="",
        validation_alias="NOCA_HEALTHMON_URL",
        description="Optional public URL shown as the footer Status link.",
    )
    HOST: str = Field(
        default="0.0.0.0",
        validation_alias="NOCA_ANIMATOR_HOST",
        description="Bind address for the animator HTTP server.",
    )
    PORT: int = Field(
        default=8003,
        gt=0,
        le=65535,
        validation_alias="NOCA_ANIMATOR_PORT",
        description="TCP port for the animator HTTP server (1-65535; default 8003).",
    )
    FORWARDED_ALLOW_IPS: str = Field(
        default="127.0.0.1,::1",
        description="Comma-separated trusted reverse-proxy IPs/CIDRs for X-Forwarded-* headers.",
    )
    POLL_FALLBACK_SECONDS: int = Field(
        default=15,
        ge=1,
        le=3600,
        validation_alias="NOCA_ANIMATOR_POLL_FALLBACK_SECONDS",
        description=(
            "Fallback interval in seconds for clients to re-poll the snapshot when the "
            "live event stream is unavailable (1 s - 1 h; default 15 s)."
        ),
    )
    ENABLE_CONTROL: bool = Field(
        default=False,
        validation_alias="NOCA_ANIMATOR_ENABLE_CONTROL",
        description=(
            "Operational kill-switch for the reveal control endpoints. When false, all "
            "reveal control routes are disabled regardless of operator secrets."
        ),
    )
    REVEAL_TTL_MARGIN_SECONDS: int = Field(
        default=3600,
        ge=60,
        le=86400,
        validation_alias="NOCA_ANIMATOR_REVEAL_TTL_MARGIN_SECONDS",
        description=(
            "Margin in seconds added to a reveal session's Valkey TTL beyond the contest "
            "end instant, so a ceremony run after the contest ends keeps its persisted state "
            "alive. The TTL is refreshed on every successful mutation (60 s - 24 h; default 1 h)."
        ),
    )

    # ------------------------------------------------------------------
    # Worker presence
    # ------------------------------------------------------------------
    WORKER_ID: str = Field(
        default="",
        validation_alias="NOCA_ANIMATOR_WORKER_ID",
        description="Stable worker ID; defaults to '<fqdn>:<pid>' when empty.",
    )
    WORKER_PRESENCE_INTERVAL_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        validation_alias="NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS",
        description="Seconds between Valkey worker-presence heartbeats.",
    )
    WORKER_PRESENCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        validation_alias="NOCA_ANIMATOR_WORKER_PRESENCE_TTL_SECONDS",
        description=(
            "TTL for the Valkey worker-presence live marker. "
            "Must be greater than NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS."
        ),
    )

    # ------------------------------------------------------------------
    # Valkey
    # ------------------------------------------------------------------
    VALKEY_SERVER: str | None = Field(default="127.0.0.1", description="Valkey/Redis host")
    VALKEY_PORT: int = Field(default=6379, gt=0, le=65535, description="Valkey port (1-65535)")
    VALKEY_DB: int = Field(default=0, ge=0, description="Valkey logical database index")
    VALKEY_USER: str | None = Field(default=None, description="Valkey ACL username")
    VALKEY_PASSWORD: str | None = Field(default=None, description="Valkey password")
    VALKEY_HEALTHCHECK_INTERVAL_SECONDS: int = Field(
        default=5,
        ge=1,
        le=300,
        description="Valkey ping/reconnect interval in seconds",
    )
    STARTUP_TIMEOUT_SECONDS: int = Field(
        default=60,
        ge=0,
        le=300,
        description="Seconds to wait for PostgreSQL and Valkey before aborting startup (0 = no wait)",
    )

    # ------------------------------------------------------------------
    # Health endpoint rate limiting
    # ------------------------------------------------------------------
    HEALTH_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        description="Enable public /health endpoint rate limiting.",
    )
    HEALTH_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        description="Fixed-window length in seconds for /health rate limiting.",
    )
    HEALTH_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=30,
        ge=1,
        description="Maximum public /health requests per client IP in each window.",
    )
    HEALTH_RATE_LIMIT_TRUSTED_CIDRS: str = Field(
        default="127.0.0.0/8,::1/128",
        description="Comma-separated CIDRs exempt from /health rate limiting.",
    )

    @model_validator(mode="after")
    def validate_worker_presence_settings(self) -> Settings:
        """Require the live-marker TTL to exceed the heartbeat interval."""
        if self.WORKER_PRESENCE_TTL_SECONDS <= self.WORKER_PRESENCE_INTERVAL_SECONDS:
            raise ValueError(
                "NOCA_ANIMATOR_WORKER_PRESENCE_TTL_SECONDS must be greater than "
                "NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS."
            )
        return self

    @field_validator("FORWARDED_ALLOW_IPS", mode="after")
    @classmethod
    def normalize_forwarded_allow_ips(cls, v: str) -> str:
        """Normalize the trusted proxy list for uvicorn forwarded-header support.

        Uvicorn rewrites ``request.client.host`` from ``X-Forwarded-For`` only for
        peers in this list, which is what makes the control API's logged client IP
        the operator rather than the reverse proxy. Same contract and validation as
        the web and arena modules.
        """
        normalized_parts: list[str] = []
        for raw_part in v.split(","):
            part = raw_part.strip()
            if not part:
                continue
            if part == "*":
                normalized_parts.append(part)
                continue
            try:
                if "/" in part:
                    ip_network(part, strict=False)
                else:
                    ip_address(part)
            except ValueError as exc:
                raise ValueError(
                    f"NOCA_FORWARDED_ALLOW_IPS must contain valid IPs, CIDRs, or '*' only. Invalid value: '{part}'"
                ) from exc
            normalized_parts.append(part)

        normalized = ",".join(normalized_parts)
        if not normalized:
            raise ValueError("NOCA_FORWARDED_ALLOW_IPS cannot be empty.")
        if "*" in normalized_parts and len(normalized_parts) > 1:
            raise ValueError("NOCA_FORWARDED_ALLOW_IPS cannot combine '*' with specific IPs/CIDRs.")
        return normalized

    @field_validator("HEALTH_RATE_LIMIT_TRUSTED_CIDRS", mode="after")
    @classmethod
    def normalize_health_rate_limit_trusted_cidrs(cls, v: str) -> str:
        """Normalize trusted CIDRs for health endpoint rate-limit bypass."""
        normalized_parts: list[str] = []
        for raw_part in v.split(","):
            part = raw_part.strip()
            if not part:
                continue
            try:
                ip_network(part, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS must contain valid CIDRs only. Invalid value: '{part}'"
                ) from exc
            normalized_parts.append(part)
        normalized = ",".join(normalized_parts)
        if not normalized:
            raise ValueError("NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS cannot be empty.")
        return normalized

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str | None) -> str | None:
        """Validate NOCA_LOG_LEVEL against the standard logging levels."""
        if v is None:
            return None
        upper = v.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if upper not in allowed:
            raise ValueError(f"NOCA_LOG_LEVEL must be one of {allowed}, got '{v}'")
        return upper

    @property
    def resolved_log_level(self) -> int:
        """Effective logging level: explicit NOCA_LOG_LEVEL, else env-based default."""
        if self.LOG_LEVEL is not None:
            return int(getattr(logging, self.LOG_LEVEL))
        return logging.INFO if self.ENVIRONMENT == Environment.PRODUCTION else logging.DEBUG

    @property
    def db_url(self) -> str:
        """Async-compatible PostgreSQL URL for SQLAlchemy."""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def valkey_url(self) -> str:
        """Redis-protocol URL for Valkey connections."""
        auth = ""
        if self.VALKEY_USER and self.VALKEY_PASSWORD:
            auth = f"{self.VALKEY_USER}:{self.VALKEY_PASSWORD}@"
        elif self.VALKEY_PASSWORD:
            auth = f":{self.VALKEY_PASSWORD}@"
        return f"redis://{auth}{self.VALKEY_SERVER}:{self.VALKEY_PORT}/{self.VALKEY_DB}"


settings = Settings()  # type: ignore[call-arg]
