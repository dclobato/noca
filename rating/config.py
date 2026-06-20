#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Rating worker settings.

Reads the same ``NOCA_``-prefixed ``.env`` file as the web and arena modules,
but only the subset of variables the rating recomputation loops need.
"""

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment


class Settings(BaseSettings):
    """Rating worker runtime configuration."""

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
    RATING_WORKER_ID: str = Field(
        default="",
        description="Stable worker ID; defaults to '<fqdn>:<pid>' when empty.",
    )
    RATING_PRESENCE_INTERVAL_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Seconds between Valkey worker-presence heartbeats.",
    )
    RATING_PRESENCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        description="TTL for the Valkey worker-presence live marker.",
    )

    # ------------------------------------------------------------------
    # Rating
    # ------------------------------------------------------------------
    RATING_INTERVAL: int = Field(
        default=86400,
        ge=900,
        le=604800,
        description="Interval in seconds between Arena rating recomputation cycles (15 min – 1 week; default 24 h).",
    )
    COMPUTE_RATINGS_ON_STARTUP: bool = Field(
        default=False,
        validation_alias="NOCA_RATING_COMPUTE_ON_STARTUP",
        description=(
            "When True, problem and user rating cycles run immediately at startup "
            "instead of waiting for the first RATING_INTERVAL to elapse. Also applies "
            "to the problem-statistics cycle."
        ),
    )
    STATS_INTERVAL: int = Field(
        default=86400,
        ge=900,
        le=604800,
        validation_alias="NOCA_RATING_STATS_INTERVAL",
        description=(
            "Interval in seconds between Arena per-problem statistics recomputation "
            "cycles (15 min – 1 week; default 24 h). The statistics loop runs "
            "independently of the rating chain."
        ),
    )
    AFFILIATION_RATING_FACTOR: float = Field(
        default=5.0,
        ge=2.0,
        le=50.0,
        validation_alias="NOCA_RATING_AFFILIATION_FACTOR",
        description=(
            "Geometric decay factor f used in affiliation rating formula "
            "S = (1/f) * Σ (1-1/f)^i * s_i. "
            "Larger f = slower weight decay = more members contribute meaningfully. "
            "Valid range: 2–50; default 5."
        ),
    )

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

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

    @field_validator("RATING_PRESENCE_TTL_SECONDS")
    @classmethod
    def validate_presence_ttl(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        """Require the live-marker TTL to exceed the heartbeat interval."""
        interval = info.data.get("RATING_PRESENCE_INTERVAL_SECONDS")
        if interval is not None and v <= float(interval):
            raise ValueError("RATING_PRESENCE_TTL_SECONDS must be greater than RATING_PRESENCE_INTERVAL_SECONDS.")
        return v

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
