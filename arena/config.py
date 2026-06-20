#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena module settings.

Reads configuration from environment variables with the ``NOCA_`` prefix (same
.env file as the web module).  Encryption-key settings are intentionally absent:
the secrets-manager library reads its own ``ENCRYPTION_KEYS__*``,
``ENCRYPTION_SALT__*``, and ``ACTIVE_ENCRYPTION_VERSION`` variables directly via
``SecretsConfig.from_environment()``.
"""

import logging
import os
from ipaddress import ip_address, ip_network
from pathlib import Path

from pydantic import DirectoryPath, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment
from shared.services.testcase_files import ARENA_TC_SUBDIR


class Settings(BaseSettings):
    """Arena runtime configuration."""

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
    APP_NAME: str = Field(
        default="noca-arena",
        validation_alias="NOCA_ARENA_APP_NAME",
        description=(
            "Application name; also used as the JWT issuer claim.  Differs from the web "
            "module default ('noca') so tokens issued by each server are not mutually valid."
        ),
    )
    ARENA_URL_BASE: str | None = Field(
        default=None,
        description=(
            "Public base URL for absolute links in emails "
            "(e.g. https://arena.example.com).  Trailing slash is stripped."
        ),
    )
    FORWARDED_ALLOW_IPS: str = Field(
        default="127.0.0.1,::1",
        description="Comma-separated trusted reverse-proxy IPs/CIDRs for X-Forwarded-* headers.",
    )
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
    # JWT
    # ------------------------------------------------------------------
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_SECONDS: int = 3600
    JWT_REFRESH_MAX_SESSION_SECONDS: int = Field(
        default=0,
        ge=0,
        description="Absolute session cap in seconds; 0 disables the cap.",
    )
    COOKIE_SECURE: bool = False

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    SEND_EMAIL: bool = Field(default=False, description="Enable real email delivery")
    EMAIL_PROVIDER: str = Field(default="mock", description="Email backend: 'mock' or 'smtp'")
    EMAIL_SENDER: str = Field(default="no-reply@noca.local", description="Default From address")
    EMAIL_SENDER_NAME: str | None = Field(default=None, description="Optional From display name")
    SMTP_SERVER: str | None = Field(default=None, description="SMTP server hostname")
    SMTP_PORT: int = Field(default=587, gt=0, le=65535, description="SMTP port (1-65535)")
    SMTP_USE_TLS: bool = Field(default=True, description="Use STARTTLS")
    SMTP_USERNAME: str | None = Field(default=None, description="SMTP username")
    SMTP_PASSWORD: str | None = Field(default=None, description="SMTP password")
    EMAIL_MBOX_LOG_DIR: str | None = Field(
        default=None,
        description="Directory for the mbox audit log of sent emails; empty disables logging",
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
    # Secrets-manager
    # ------------------------------------------------------------------
    ARENA_CRYPTO_ENV_FILE: str = Field(
        default=".env.crypto",
        validation_alias="NOCA_CRYPTO_ENV_FILE",
        description=(
            "Path to the dotenv file that holds secrets-manager variables "
            "(ENCRYPTION_KEYS__*, ENCRYPTION_SALT__*, ACTIVE_ENCRYPTION_VERSION). "
            "Loaded into os.environ at startup before SecretsConfig.from_environment() is called."
        ),
    )

    # ------------------------------------------------------------------
    # Geolocation (used by login-history recording in the auth batch)
    # ------------------------------------------------------------------
    GEOLOCATION_API_KEY: str | None = Field(
        default=None,
        description="API key for IP geolocation (optional; required for login-location recording)",
    )
    ARENA_REVERSE_GEOCODER_ENABLED: bool = Field(
        default=True,
        description="Enable browser-coordinate reverse geocoding for Arena profile location detection.",
    )
    ARENA_REVERSE_GEOCODER_URL: str = Field(
        default="https://nominatim.openstreetmap.org/reverse",
        description="Nominatim-compatible reverse-geocoder endpoint used by Arena profile location detection.",
    )
    ARENA_REVERSE_GEOCODER_USER_AGENT: str | None = Field(
        default=None,
        description="Optional User-Agent for reverse geocoder requests; defaults to app name/version.",
    )

    # ------------------------------------------------------------------
    # Password policy
    # ------------------------------------------------------------------
    ARENA_PASSWORD_MAX_AGE: int = Field(
        default=0,
        ge=0,
        description=("Maximum password age in days before a warning is shown at login. 0 disables the check entirely."),
    )
    WORDLIST_FILENAME: str = Field(
        default="wordlist-pt.txt",
        description="Filename of the wordlist used for diceware password generation.",
    )
    PASSWORD_WORD_COUNT: int = Field(
        default=4,
        ge=1,
        description="Number of words used when generating diceware passwords.",
    )
    MIN_PASSWORD_LENGTH: int = Field(
        default=12,
        ge=8,
        description="Minimum character length required for any password.",
    )
    PASSWORD_UPPERCASE_REQUIRED: bool = Field(
        default=True,
        description="Whether passwords must include at least one uppercase letter.",
    )
    PASSWORD_LOWERCASE_REQUIRED: bool = Field(
        default=True,
        description="Whether passwords must include at least one lowercase letter.",
    )
    PASSWORD_NUMBER_REQUIRED: bool = Field(
        default=True,
        description="Whether passwords must include at least one number.",
    )
    PASSWORD_SYMBOL_REQUIRED: bool = Field(
        default=True,
        description="Whether passwords must include at least one symbol.",
    )

    # ------------------------------------------------------------------
    # Problem test-case storage
    # ------------------------------------------------------------------
    PROBLEM_TESTCASE_DIR_ROOT: DirectoryPath = Field(
        validation_alias="NOCA_PROBLEM_TESTCASE_DIR",
        description=(
            "Root directory shared by Web, Arena, and Autojudge for problem test cases "
            "(must be readable and writable). Arena problems live under the 'arena/' subdir."
        ),
    )

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------
    IMAGE_AVATAR_SIZE: int = Field(
        default=64,
        gt=0,
        le=256,
        description="Generated avatar max size in pixels for uploaded images.",
    )
    IMAGE_MAX_FILE_SIZE: int = Field(
        default=5 * 1024 * 1024,
        gt=0,
        le=5 * 1024 * 1024,
        description="Maximum allowed uploaded image size in bytes.",
    )
    IMAGE_MAX_WIDTH: int = Field(
        default=2048,
        gt=0,
        le=4096,
        description="Maximum allowed uploaded image width in pixels.",
    )
    IMAGE_MAX_HEIGHT: int = Field(
        default=2048,
        gt=0,
        le=4096,
        description="Maximum allowed uploaded image height in pixels.",
    )
    IMAGE_FONT_DIR: str | None = Field(
        default=None,
        description="Optional directory containing fonts used by generated placeholders.",
    )
    IMAGE_RESPONSE_CACHE_MAX_AGE: int = Field(
        default=3600,
        gt=0,
        description="Cache max-age for image responses in seconds.",
    )

    # ------------------------------------------------------------------
    # Arena Admin Bootstrap
    # ------------------------------------------------------------------
    ARENA_ADMIN_FULLNAME: str = Field(
        default="",
        validation_alias="NOCA_ARENA_ADMIN_FULLNAME",
        description="Full name for the bootstrap Arena admin account.",
    )
    ARENA_ADMIN_EMAIL: str = Field(
        default="",
        validation_alias="NOCA_ARENA_ADMIN_EMAIL",
        description="Email address for the bootstrap Arena admin account.",
    )
    ARENA_ADMIN_PASSWORD: str = Field(
        default="",
        validation_alias="NOCA_ARENA_ADMIN_PASSWORD",
        description="Password for the bootstrap Arena admin account.",
    )

    # ------------------------------------------------------------------
    # Submission rate limiting
    # ------------------------------------------------------------------
    ARENA_RATE_LIMIT_WINDOW_MINUTES: int = Field(
        default=5,
        ge=1,
        description="Rolling window length in minutes for per-user submission rate limiting.",
    )
    ARENA_RATE_LIMIT_MAX_SUBMISSIONS: int = Field(
        default=10,
        ge=1,
        description="Maximum number of submissions allowed per user within the rate-limit window.",
    )
    ARENA_LIVE_FEED_LIMIT: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of finalized submissions returned by the public Arena live feed.",
    )

    # ------------------------------------------------------------------
    # AI assistant batch window (mirrors aiassistant config for display)
    # ------------------------------------------------------------------
    AI_BATCH_POLL_INTERVAL_SECONDS: float = Field(
        default=300.0,
        ge=60,
        le=3600,
        description=(
            "Must match NOCA_AI_BATCH_POLL_INTERVAL_SECONDS on the aiassistant worker. "
            "Used to compute the displayed batch window size (5× this value) shown in the "
            "AI review confirmation modal."
        ),
    )

    @property
    def ai_batch_window_minutes(self) -> int:
        """Batch accumulation window in whole minutes (5 × AI_BATCH_POLL_INTERVAL_SECONDS)."""
        return max(1, int(5 * self.AI_BATCH_POLL_INTERVAL_SECONDS // 60))

    # ------------------------------------------------------------------
    # Worker pause/resume control
    # ------------------------------------------------------------------
    WORKER_COMMAND_SECRET: str = Field(
        default="",
        validation_alias="NOCA_WORKER_COMMAND_SECRET",
        description=(
            "Shared HMAC secret for signing worker pause/resume commands. When empty, the "
            "pause/resume controls are disabled (buttons hidden, direct POSTs rejected)."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("ARENA_URL_BASE", mode="after")
    @classmethod
    def normalize_url_base(cls, v: str | None) -> str | None:
        """Strip trailing slash and validate scheme."""
        if v is None:
            return None
        v = v.strip().rstrip("/")
        if not v:
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("NOCA_ARENA_URL_BASE must start with http:// or https://")
        return v

    @field_validator("FORWARDED_ALLOW_IPS", mode="after")
    @classmethod
    def normalize_forwarded_allow_ips(cls, v: str) -> str:
        """Normalize trusted proxy list for uvicorn forwarded-header support."""
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

    @field_validator("PROBLEM_TESTCASE_DIR_ROOT", mode="after")
    @classmethod
    def check_testcase_dir_rw(cls, v: Path) -> Path:
        """Require the shared test-case root to be readable and writable."""
        if not os.access(v, os.R_OK):
            raise ValueError(f"Directory '{v}' is not readable.")
        if not os.access(v, os.W_OK):
            raise ValueError(f"Directory '{v}' is not writable.")
        return v

    @property
    def PROBLEM_TESTCASE_DIR(self) -> Path:  # noqa: N802
        """Arena test-case root: the 'arena/' subdir under the shared root.

        Created on demand by the test-case writers; namespaced so the Web and
        Arena identity domains never collide under the shared mount.
        """
        return Path(self.PROBLEM_TESTCASE_DIR_ROOT) / ARENA_TC_SUBDIR

    @model_validator(mode="after")
    def validate_email_settings(self) -> Settings:
        """Validate email configuration consistency."""
        provider = self.EMAIL_PROVIDER.casefold()
        if provider not in {"mock", "smtp"}:
            raise ValueError("NOCA_EMAIL_PROVIDER must be 'mock' or 'smtp'.")
        if not self.EMAIL_SENDER.strip():
            raise ValueError("NOCA_EMAIL_SENDER cannot be empty.")
        if not self.SEND_EMAIL or provider != "smtp":
            return self
        missing: list[str] = []
        if not self.SMTP_SERVER:
            missing.append("NOCA_SMTP_SERVER")
        if not self.SMTP_USERNAME:
            missing.append("NOCA_SMTP_USERNAME")
        if not self.SMTP_PASSWORD:
            missing.append("NOCA_SMTP_PASSWORD")
        if missing:
            raise ValueError(f"Missing required SMTP settings: {', '.join(missing)}")
        return self

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
