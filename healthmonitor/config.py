#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Health monitor settings.

Reads the same ``NOCA_``-prefixed ``.env`` file as the other modules, but only
the subset of variables the monitoring server needs: Valkey connectivity plus
its own probing/retention knobs. The module deliberately has no database or
JWT configuration -- both dashboards are public and all state lives in Valkey.
"""

import logging

from pydantic import Field, model_validator
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
    BRAND_NAME: str = Field(
        default="NOCA",
        validation_alias="NOCA_HEALTHMON_BRAND_NAME",
        description="Brand name shown on monitor pages",
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
