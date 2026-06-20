#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""AI Assistant worker settings.

Reads the same ``NOCA_``-prefixed ``.env`` file as the other NOCA modules,
covering the subset of variables the AI review worker needs.
"""

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment


class Settings(BaseSettings):
    """AI Assistant worker runtime configuration."""

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
    AI_WORKER_COMMAND_POLL_SECONDS: float = Field(
        default=3.0,
        ge=0.5,
        le=60.0,
        description="Seconds between Valkey pause/resume command-key polls.",
    )
    AI_WORKER_COMMAND_FRESHNESS_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Symmetric freshness window (seconds) for accepting a signed command.",
    )
    AI_WORKER_COMMAND_NONCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        description="TTL for the single-use command nonce; must exceed the freshness window.",
    )
    AI_WORKER_ID: str = Field(
        default="",
        description="Stable worker ID; defaults to '<fqdn>:<pid>' when empty.",
    )
    AI_PRESENCE_INTERVAL_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Seconds between Valkey worker-presence heartbeats.",
    )
    AI_PRESENCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        description="TTL for the Valkey worker-presence live marker.",
    )

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------
    OPENAI_API_KEY: str | None = Field(
        default=None,
        validation_alias="NOCA_AI_OPENAI_API_KEY",
        description=(
            "Platform-level OpenAI API key used as a fallback when the Arena user "
            "has not configured their own key. Cost is recorded in the submission "
            "when this key is used."
        ),
    )
    OPENAI_MODEL: str = Field(
        default="gpt-5.4-mini",
        validation_alias="NOCA_AI_OPENAI_MODEL",
        description="OpenAI model identifier used for AI code review.",
    )
    OPENAI_MAX_OUTPUT_TOKENS: int = Field(
        default=500,
        ge=100,
        le=4096,
        validation_alias="NOCA_AI_OPENAI_MAX_OUTPUT_TOKENS",
        description="Maximum number of output tokens per AI review response.",
    )
    OPENAI_INPUT_TOKEN_PRICE: float = Field(
        default=0.75,
        ge=0.0,
        validation_alias="NOCA_AI_OPENAI_INPUT_TOKEN_PRICE",
        description="Input token price in USD per 1 million tokens (used when recording cost).",
    )
    OPENAI_OUTPUT_TOKEN_PRICE: float = Field(
        default=4.50,
        ge=0.0,
        validation_alias="NOCA_AI_OPENAI_OUTPUT_TOKEN_PRICE",
        description="Output token price in USD per 1 million tokens (used when recording cost).",
    )

    # ------------------------------------------------------------------
    # Secrets Manager
    # ------------------------------------------------------------------
    AIASSISTANT_CRYPTO_ENV_FILE: str = Field(
        default=".env.crypto",
        validation_alias="NOCA_CRYPTO_ENV_FILE",
        description=(
            "Path to the dotenv file that holds secrets-manager variables "
            "(ENCRYPTION_KEYS__*, ENCRYPTION_SALT__*, ACTIVE_ENCRYPTION_VERSION). "
            "Loaded into os.environ at startup before SecretsConfig.from_environment() is called."
        ),
    )

    # ------------------------------------------------------------------
    # AI review worker behaviour
    # ------------------------------------------------------------------
    AI_POLL_INTERVAL_SECONDS: float = Field(
        default=5.0,
        ge=0.5,
        le=60.0,
        description="Seconds to wait between queue polls when the pending queue is empty.",
    )
    AI_STALE_THRESHOLD_SECONDS: float = Field(
        default=300.0,
        ge=30.0,
        description="Seconds after which an inflight AI review job is considered stale by the reaper.",
    )
    AI_REAPER_INTERVAL_SECONDS: float = Field(
        default=60.0,
        ge=5.0,
        description="Seconds between reaper scans for stale inflight jobs.",
    )
    AI_MAX_REQUEUE_COUNT: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Maximum number of times a stale job can be re-enqueued before being discarded.",
    )
    AI_BATCH_POLL_INTERVAL_SECONDS: float = Field(
        default=300.0,
        ge=60.0,
        le=3600.0,
        description=(
            "Seconds between batch poller scans for pending OpenAI batch jobs. "
            "Batches can take up to 24 h; 300 s (5 min) is a reasonable default."
        ),
    )
    AI_BATCH_STALE_HOURS: int = Field(
        default=24,
        ge=1,
        le=168,
        validation_alias="NOCA_AI_BATCH_STALE_HOURS",
        description=(
            "Hours after submission before a non-terminal OpenAI batch job is "
            "considered stale, locally expired, refunded, and the user notified."
        ),
    )
    AI_RECONCILER_INTERVAL_SECONDS: float = Field(
        default=120.0,
        ge=10.0,
        description="Seconds between reconciler sweeps for AI review jobs lost after commit.",
    )
    AI_RECONCILER_GRACE_SECONDS: float = Field(
        default=120.0,
        ge=10.0,
        description=(
            "Minimum age (seconds since last update) before a submission flagged "
            "submit_to_ai is eligible for reconciliation, to avoid racing a fresh "
            "request whose Valkey enqueue is still in flight."
        ),
    )
    AI_RECONCILER_BATCH_SIZE: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of lost AI review jobs re-enqueued per reconciler sweep.",
    )

    # ------------------------------------------------------------------
    # OpenAI batch pricing (defaults to 50 % of online price)
    # ------------------------------------------------------------------
    OPENAI_BATCH_INPUT_TOKEN_PRICE: float | None = Field(
        default=None,
        ge=0.0,
        validation_alias="NOCA_AI_OPENAI_BATCH_INPUT_TOKEN_PRICE",
        description=(
            "Input token price in USD per 1 million tokens for batch requests. "
            "Defaults to half of OPENAI_INPUT_TOKEN_PRICE when not set."
        ),
    )
    OPENAI_BATCH_OUTPUT_TOKEN_PRICE: float | None = Field(
        default=None,
        ge=0.0,
        validation_alias="NOCA_AI_OPENAI_BATCH_OUTPUT_TOKEN_PRICE",
        description=(
            "Output token price in USD per 1 million tokens for batch requests. "
            "Defaults to half of OPENAI_OUTPUT_TOKEN_PRICE when not set."
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

    @field_validator("AI_PRESENCE_TTL_SECONDS")
    @classmethod
    def validate_presence_ttl(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        """Require the live-marker TTL to exceed the heartbeat interval."""
        interval = info.data.get("AI_PRESENCE_INTERVAL_SECONDS")
        if interval is not None and v <= float(interval):
            raise ValueError("AI_PRESENCE_TTL_SECONDS must be greater than AI_PRESENCE_INTERVAL_SECONDS.")
        return v

    @field_validator("AI_WORKER_COMMAND_NONCE_TTL_SECONDS")
    @classmethod
    def validate_command_nonce_ttl(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        """Require the nonce TTL to outlast the command freshness window."""
        freshness = info.data.get("AI_WORKER_COMMAND_FRESHNESS_SECONDS")
        if freshness is not None and v <= float(freshness):
            raise ValueError("AI_WORKER_COMMAND_NONCE_TTL_SECONDS must exceed AI_WORKER_COMMAND_FRESHNESS_SECONDS.")
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
    def effective_batch_input_price(self) -> float:
        """Input token price for batch requests (defaults to 50 % of online price)."""
        return (
            self.OPENAI_BATCH_INPUT_TOKEN_PRICE
            if self.OPENAI_BATCH_INPUT_TOKEN_PRICE is not None
            else (self.OPENAI_INPUT_TOKEN_PRICE / 2)
        )

    @property
    def effective_batch_output_price(self) -> float:
        """Output token price for batch requests (defaults to 50 % of online price)."""
        return (
            self.OPENAI_BATCH_OUTPUT_TOKEN_PRICE
            if self.OPENAI_BATCH_OUTPUT_TOKEN_PRICE is not None
            else (self.OPENAI_OUTPUT_TOKEN_PRICE / 2)
        )


settings = Settings()  # type: ignore[call-arg]
