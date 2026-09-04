#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Health monitor settings.

Reads the same ``NOCA_``-prefixed ``.env`` file as the other modules, but only
the subset of variables the monitoring server needs: Valkey connectivity plus
its own probing/retention knobs. The module deliberately has no database or
JWT configuration -- the dashboard is public and all state lives in Valkey.

Monitor-specific fields declare an explicit ``validation_alias`` so they resolve
to ``NOCA_HEALTHMON_*`` (the alias replaces the ``env_prefix``, avoiding an
accidental ``NOCA_NOCA_HEALTHMON_*`` double prefix).
"""

import logging
from ipaddress import ip_address, ip_network

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment


class Settings(BaseSettings):
    """Health monitor runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="NOCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
    HOST: str = Field(
        default="0.0.0.0",
        validation_alias="NOCA_HEALTHMON_HOST",
        description=(
            "Bind address for the health monitor HTTP server. Container deployments must keep "
            "0.0.0.0: the reverse proxy reaches the service over the container network and the "
            "container healthcheck probes loopback."
        ),
    )
    PORT: int = Field(
        default=8002,
        gt=0,
        le=65535,
        validation_alias="NOCA_HEALTHMON_PORT",
        description="TCP port for the health monitor HTTP server (1-65535; default 8002).",
    )
    FORWARDED_ALLOW_IPS: str = Field(
        default="127.0.0.1,::1",
        description="Comma-separated trusted reverse-proxy IPs/CIDRs for X-Forwarded-* headers.",
    )
    BRAND_NAME: str = Field(
        default="NOCA",
        validation_alias="NOCA_HEALTHMON_BRAND_NAME",
        description="Brand name shown on monitor pages",
    )
    # Deliberately unprefixed (NOCA_SECURITY_HEADERS_ENABLED / NOCA_CSP_REPORT_ONLY)
    # so one setting governs the header policy of every HTTP module at once,
    # exactly as web and arena already read it.
    SECURITY_HEADERS_ENABLED: bool = Field(
        default=True,
        description="Enable shared browser security headers.",
    )
    CSP_REPORT_ONLY: bool = Field(
        default=True,
        description="Send Content-Security-Policy-Report-Only instead of enforcing CSP.",
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
        description="Seconds to wait for Valkey before aborting startup (0 = no wait)",
    )

    # ------------------------------------------------------------------
    # Probing and retention
    # ------------------------------------------------------------------
    PROBE_INTERVAL: int = Field(
        default=300,
        ge=30,
        le=3600,
        validation_alias="NOCA_HEALTHMON_PROBE_INTERVAL",
        description=(
            "Interval in seconds between up/down probes of the monitored services (30 s - 1 h; default 5 min)."
        ),
    )
    REAPER_INTERVAL: int = Field(
        default=43200,
        ge=3600,
        le=604800,
        validation_alias="NOCA_HEALTHMON_REAPER_INTERVAL",
        description=(
            "Interval in seconds between cleanup passes that delete uptime slots "
            "older than the retention window (1 h - 1 week; default 12 h)."
        ),
    )
    RETENTION_DAYS: int = Field(
        default=30,
        ge=7,
        le=90,
        validation_alias="NOCA_HEALTHMON_RETENTION_DAYS",
        description="Days of per-slot uptime history kept for the heatmap (7-90; default 30).",
    )

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    # Deliberately unprefixed (NOCA_HEALTH_RATE_LIMIT_*): the same four settings
    # govern the /health limiter of every HTTP module.
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
    RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_HEALTHMON_RATE_LIMIT_ENABLED",
        description="Enable per-IP rate limiting of the public dashboard routes (/, /refresh, /uptime.json).",
    )
    RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=120,
        ge=1,
        validation_alias="NOCA_HEALTHMON_RATE_LIMIT_MAX_REQUESTS",
        description=(
            "Maximum dashboard requests per client IP in each fixed window, shared by /, /refresh "
            "and /uptime.json. One idle tab issues four requests per minute."
        ),
    )
    RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        validation_alias="NOCA_HEALTHMON_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for dashboard rate limiting.",
    )
    RATE_LIMIT_TRUSTED_CIDRS: str = Field(
        default="127.0.0.0/8,::1/128",
        validation_alias="NOCA_HEALTHMON_RATE_LIMIT_TRUSTED_CIDRS",
        description="Comma-separated CIDRs exempt from dashboard rate limiting.",
    )

    @field_validator("HEALTH_RATE_LIMIT_TRUSTED_CIDRS", "RATE_LIMIT_TRUSTED_CIDRS", mode="after")
    @classmethod
    def normalize_rate_limit_trusted_cidrs(cls, v: str, info: ValidationInfo) -> str:
        """Normalize a trusted-CIDR bypass list and reject empty or invalid entries."""
        variable = (
            "NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS"
            if info.field_name == "HEALTH_RATE_LIMIT_TRUSTED_CIDRS"
            else "NOCA_HEALTHMON_RATE_LIMIT_TRUSTED_CIDRS"
        )
        normalized_parts: list[str] = []
        for raw_part in v.split(","):
            part = raw_part.strip()
            if not part:
                continue
            try:
                ip_network(part, strict=False)
            except ValueError as exc:
                raise ValueError(f"{variable} must contain valid CIDRs only. Invalid value: '{part}'") from exc
            normalized_parts.append(part)
        normalized = ",".join(normalized_parts)
        if not normalized:
            raise ValueError(f"{variable} cannot be empty.")
        return normalized

    @field_validator("FORWARDED_ALLOW_IPS", mode="after")
    @classmethod
    def normalize_forwarded_allow_ips(cls, v: str) -> str:
        """Normalize the trusted proxy list for uvicorn forwarded-header support.

        Uvicorn rewrites ``request.client.host`` from ``X-Forwarded-For`` only for
        peers in this list. Same contract and validation as the web, arena, and
        animator modules.
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

    @model_validator(mode="after")
    def validate_intervals(self) -> Settings:
        """Keep the reaper slower than the probe so slots are never reaped mid-fill."""
        if self.REAPER_INTERVAL < self.PROBE_INTERVAL:
            raise ValueError(
                "NOCA_HEALTHMON_REAPER_INTERVAL must be greater than or equal to NOCA_HEALTHMON_PROBE_INTERVAL."
            )
        return self

    @property
    def resolved_log_level(self) -> int:
        """Effective logging level: explicit NOCA_LOG_LEVEL, else env-based default."""
        if self.LOG_LEVEL is not None:
            return int(getattr(logging, self.LOG_LEVEL))
        return logging.INFO if self.ENVIRONMENT == Environment.PRODUCTION else logging.DEBUG

    @property
    def valkey_url(self) -> str:
        """Redis-protocol URL for Valkey connections."""
        auth = ""
        if self.VALKEY_USER and self.VALKEY_PASSWORD:
            auth = f"{self.VALKEY_USER}:{self.VALKEY_PASSWORD}@"
        elif self.VALKEY_PASSWORD:
            auth = f":{self.VALKEY_PASSWORD}@"
        return f"redis://{auth}{self.VALKEY_SERVER}:{self.VALKEY_PORT}/{self.VALKEY_DB}"


settings = Settings()
