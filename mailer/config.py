#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Mailer worker settings.

Reads the same ``NOCA_``-prefixed ``.env`` file as the other NOCA modules. The
email block uses exactly the names Web and Arena use (``NOCA_SEND_EMAIL``,
``NOCA_EMAIL_PROVIDER``, ``NOCA_SMTP_*``, ``NOCA_EMAIL_MBOX_LOG_DIR``), so one
``.env`` configures the provider once for every process; the worker-specific
knobs live under ``NOCA_MAILER_*``.
"""

import logging
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment


class Settings(BaseSettings):
    """Mailer worker runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="NOCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Database (pause-state reconciliation only; the worker never touches
    # application tables)
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

    # ------------------------------------------------------------------
    # Email provider (shared names with Web and Arena)
    # ------------------------------------------------------------------
    SEND_EMAIL: bool = Field(
        default=False,
        description=(
            "Deliver for real. When false (or the provider is mock) the worker still drains the "
            "queue, but each message goes to its log instead of a mail server."
        ),
    )
    EMAIL_PROVIDER: str = Field(default="mock", description="Email backend: 'mock' or 'smtp'")
    EMAIL_SENDER: str = Field(default="no-reply@noca.local", description="Default From address")
    EMAIL_SENDER_NAME: str | None = Field(
        default=None,
        description="Optional From display name (falls back to BRAND_NAME)",
    )
    BRAND_NAME: str = Field(
        default="NOCA",
        validation_alias="NOCA_MAILER_BRAND_NAME",
        description="Fallback From display name when NOCA_EMAIL_SENDER_NAME is empty.",
    )
    SMTP_SERVER: str | None = Field(default=None, description="SMTP server hostname")
    SMTP_PORT: int = Field(default=587, gt=0, le=65535, description="SMTP port (1-65535)")
    SMTP_USE_TLS: bool = Field(default=True, description="Use STARTTLS")
    SMTP_USERNAME: str | None = Field(default=None, description="SMTP username")
    SMTP_PASSWORD: str | None = Field(default=None, description="SMTP password")
    EMAIL_MBOX_LOG_DIR: str | None = Field(
        default=None,
        description="Directory for the mbox audit log of sent emails; empty disables logging",
    )
    EMAIL_QUEUE_JOB_TTL_SECONDS: int = Field(
        default=3600,
        ge=60,
        description=(
            "Seconds a queued email may wait for this worker. A job older than this when it is "
            "dequeued is dropped unsent (its hash also expires on its own). Keep it equal to the "
            "Web/Arena value."
        ),
    )
    EMAIL_BUDGET_ENABLED: bool = Field(
        default=False, description="Unused by the worker (budgets are charged at enqueue)."
    )
    EMAIL_BUDGET_WINDOW_SECONDS: int = Field(default=600, ge=1, description="Unused by the worker.")
    EMAIL_BUDGET_USER_MAX: int = Field(default=20, ge=0, description="Unused by the worker.")
    EMAIL_BUDGET_ADMIN_MAX: int = Field(default=200, ge=0, description="Unused by the worker.")

    # ------------------------------------------------------------------
    # Worker pause/resume control
    # ------------------------------------------------------------------
    WORKER_COMMAND_SECRET: str = Field(
        default="",
        validation_alias="NOCA_WORKER_COMMAND_SECRET",
        description=(
            "Shared HMAC secret for verifying pause/resume commands. When empty, the worker "
            "command loop is disabled and the worker never honors remote pause nudges."
        ),
    )
    MAILER_WORKER_COMMAND_POLL_SECONDS: float = Field(
        default=3.0,
        ge=0.5,
        le=60.0,
        description="Seconds between Valkey pause/resume command-key polls.",
    )
    MAILER_WORKER_COMMAND_FRESHNESS_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Symmetric freshness window (seconds) for accepting a signed command.",
    )
    MAILER_WORKER_COMMAND_NONCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        description="TTL for the single-use command nonce; must exceed the freshness window.",
    )
    MAILER_WORKER_ID: str = Field(
        default="",
        description="Stable worker ID; defaults to '<fqdn>:<pid>' when empty.",
    )
    MAILER_PRESENCE_INTERVAL_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Seconds between Valkey worker-presence heartbeats.",
    )
    MAILER_PRESENCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        description="TTL for the Valkey worker-presence live marker.",
    )
    MAILER_HEARTBEAT_FILE: str = Field(
        default="/tmp/mailer-heartbeat",
        description="Absolute path touched periodically while the worker process is healthy.",
    )
    MAILER_HEARTBEAT_INTERVAL_SECONDS: float = Field(
        default=10.0,
        ge=1.0,
        le=300.0,
        description="How often the worker refreshes its heartbeat file.",
    )
    MAILER_HEARTBEAT_STALE_SECONDS: float = Field(
        default=30.0,
        ge=2.0,
        le=3600.0,
        description="Maximum heartbeat-file age before the container is considered unhealthy.",
    )

    # ------------------------------------------------------------------
    # Mail delivery behaviour
    # ------------------------------------------------------------------
    MAILER_POLL_INTERVAL_SECONDS: float = Field(
        default=2.0,
        ge=0.5,
        le=60.0,
        description="Seconds to wait between queue polls when the pending queue is empty.",
    )
    MAILER_MAX_PER_MINUTE: int = Field(
        default=60,
        ge=1,
        le=6000,
        description=(
            "Deployment-wide delivery pace: the worker sleeps 60 / this many seconds after every "
            "attempt, so no burst of requests can exceed it at the provider. Size it to the plan."
        ),
    )
    MAILER_STALE_THRESHOLD_SECONDS: float = Field(
        default=300.0,
        ge=30.0,
        description=(
            "Seconds after which an inflight job is considered stale by the reaper. A job the "
            "provider refused is left inflight on purpose, so this is also the retry delay."
        ),
    )
    MAILER_REAPER_INTERVAL_SECONDS: float = Field(
        default=60.0,
        ge=5.0,
        description="Seconds between reaper scans for stale inflight jobs.",
    )
    MAILER_MAX_REQUEUE_COUNT: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Maximum number of times a stale job can be re-enqueued before being dropped.",
    )

    @field_validator("EMAIL_MBOX_LOG_DIR", mode="after")
    @classmethod
    def normalize_mbox_log_dir(cls, v: str | None) -> str | None:
        """Treat empty/blank values as disabled and require an absolute path."""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not Path(v).is_absolute():
            raise ValueError("NOCA_EMAIL_MBOX_LOG_DIR must be an absolute path.")
        return v

    @model_validator(mode="after")
    def validate_email_settings(self) -> Settings:
        """Real SMTP delivery needs the relay and its credentials; nothing else does."""
        provider = self.EMAIL_PROVIDER.casefold()
        if provider not in {"mock", "smtp"}:
            raise ValueError("NOCA_EMAIL_PROVIDER must be 'mock' or 'smtp'.")
        if not self.EMAIL_SENDER.strip():
            raise ValueError("NOCA_EMAIL_SENDER cannot be empty.")
        if not self.SEND_EMAIL or provider != "smtp":
            return self
        missing = [
            name
            for name, value in (
                ("NOCA_SMTP_SERVER", self.SMTP_SERVER),
                ("NOCA_SMTP_USERNAME", self.SMTP_USERNAME),
                ("NOCA_SMTP_PASSWORD", self.SMTP_PASSWORD),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required SMTP settings: {', '.join(missing)}")
        return self

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

    @field_validator("MAILER_PRESENCE_TTL_SECONDS")
    @classmethod
    def validate_presence_ttl(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        """Require the live-marker TTL to exceed the heartbeat interval."""
        interval = info.data.get("MAILER_PRESENCE_INTERVAL_SECONDS")
        if interval is not None and v <= float(interval):
            raise ValueError("MAILER_PRESENCE_TTL_SECONDS must be greater than MAILER_PRESENCE_INTERVAL_SECONDS.")
        return v

    @field_validator("MAILER_WORKER_COMMAND_NONCE_TTL_SECONDS")
    @classmethod
    def validate_command_nonce_ttl(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        """Require the nonce TTL to outlast the command freshness window."""
        freshness = info.data.get("MAILER_WORKER_COMMAND_FRESHNESS_SECONDS")
        if freshness is not None and v <= float(freshness):
            raise ValueError(
                "MAILER_WORKER_COMMAND_NONCE_TTL_SECONDS must exceed MAILER_WORKER_COMMAND_FRESHNESS_SECONDS."
            )
        return v

    @field_validator("MAILER_HEARTBEAT_FILE")
    @classmethod
    def validate_heartbeat_file(cls, v: str) -> str:
        """Require an absolute heartbeat path so the healthcheck resolves the same file."""
        if not v.startswith("/"):
            raise ValueError("NOCA_MAILER_HEARTBEAT_FILE must be an absolute path.")
        return v

    @field_validator("MAILER_HEARTBEAT_STALE_SECONDS")
    @classmethod
    def validate_heartbeat_stale(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        """Require the staleness threshold to exceed the refresh interval."""
        interval = info.data.get("MAILER_HEARTBEAT_INTERVAL_SECONDS")
        if interval is not None and v <= float(interval):
            raise ValueError(
                "NOCA_MAILER_HEARTBEAT_STALE_SECONDS must be greater than NOCA_MAILER_HEARTBEAT_INTERVAL_SECONDS."
            )
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

    @property
    def send_interval_seconds(self) -> float:
        """Seconds the worker rests after each delivery attempt (the global pace)."""
        return 60.0 / self.MAILER_MAX_PER_MINUTE


settings = Settings()  # type: ignore[call-arg]
