#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import logging
import os
from ipaddress import ip_address, ip_network
from pathlib import Path

from pydantic import DirectoryPath, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment
from shared.services.testcase_files import CONTEST_TC_SUBDIR


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DB_USER: str
    DB_PASSWORD: str
    DB_SERVER: str
    DB_PORT: int = Field(default=5432, gt=0, le=65535, description="Port number for the database server (1 to 65535)")
    DB_NAME: str

    APP_NAME: str = Field(default="noca", validation_alias="NOCA_WEB_APP_NAME")
    WEB_URL_BASE: str | None = Field(
        default=None,
        description=(
            "Public base URL used to build absolute links in emails and downloadable reports "
            "(e.g. https://contest.example.com or http://192.168.1.10:8000). "
            "Must include scheme and host; trailing slash is stripped automatically. "
            "When not set, links are derived from the incoming HTTP request, which may produce "
            "incorrect URLs behind a reverse proxy that does not forward X-Forwarded-* headers."
        ),
    )
    FORWARDED_ALLOW_IPS: str = Field(
        default="127.0.0.1,::1",
        description=(
            "Comma-separated list of trusted reverse proxy IPs/CIDRs used to accept "
            "X-Forwarded-* headers (e.g. 127.0.0.1,10.0.0.0/8). Use '*' only in trusted "
            "private networks where requests cannot come directly from untrusted clients."
        ),
    )
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_SECONDS: int = 3600
    JWT_REFRESH_MAX_SESSION_SECONDS: int = Field(
        default=0,
        ge=0,
        description=(
            "Optional absolute cap for sliding web sessions in seconds. "
            "Set to 0 to disable the cap and keep active users signed in indefinitely."
        ),
    )
    COOKIE_SECURE: bool = False
    SEND_EMAIL: bool = Field(default=False, description="Enable real email sending in web layer")
    EMAIL_PROVIDER: str = Field(default="mock", description="Email provider backend: mock or smtp")
    EMAIL_SENDER: str = Field(
        default="no-reply@noca.local",
        description="Default sender email used by EmailService",
    )
    EMAIL_SENDER_NAME: str | None = Field(
        default=None,
        description="Optional default sender display name (falls back to APP_NAME)",
    )
    SMTP_SERVER: str | None = Field(default=None, description="SMTP server hostname")
    SMTP_PORT: int = Field(default=587, gt=0, le=65535, description="SMTP server port (1 to 65535)")
    SMTP_USE_TLS: bool = Field(default=True, description="Whether SMTP should use STARTTLS")
    SMTP_USERNAME: str | None = Field(default=None, description="SMTP username")
    SMTP_PASSWORD: str | None = Field(default=None, description="SMTP password")
    EMAIL_MBOX_LOG_DIR: str | None = Field(
        default=None,
        description="Directory for the mbox audit log of sent emails; empty disables logging",
    )
    ENABLE_CLARIFICATION_REAPER: bool = Field(
        default=False,
        validation_alias="NOCA_WEB_ENABLE_CLARIFICATION_REAPER",
        description="Whether the in-process clarification reaper background task should run.",
    )
    CLARIFICATION_REAPER_INTERVAL_SECONDS: int = Field(
        default=1800,
        ge=180,
        le=1800,
        validation_alias="NOCA_WEB_CLARIFICATION_REAPER_INTERVAL_SECONDS",
        description="Polling interval for the clarification reaper in seconds (3 to 30 minutes).",
    )

    ENABLE_TASK_REAPER: bool = Field(
        default=False,
        validation_alias="NOCA_WEB_ENABLE_TASK_REAPER",
        description="Whether the in-process task reaper background task should run.",
    )
    TASK_REAPER_INTERVAL_SECONDS: int = Field(
        default=1800,
        ge=180,
        le=1800,
        validation_alias="NOCA_WEB_TASK_REAPER_INTERVAL_SECONDS",
        description="Polling interval for the task reaper in seconds (3 to 30 minutes).",
    )
    SHOW_COMPILE_RUN_CMDS: bool = Field(
        default=False,
        validation_alias="NOCA_WEB_SHOW_COMPILE_RUN_CMDS",
        description="Show compile and run commands for each language in the submission form.",
    )
    WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        description="Rolling window in seconds for per-team submission rate limiting.",
    )
    WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS: int = Field(
        default=3,
        ge=1,
        description="Maximum submissions a team may send within the rate-limit window.",
    )
    UBERADMIN_USERNAME: str = Field(
        default="",
        validation_alias="NOCA_WEB_UBERADMIN_USERNAME",
        description="Username for the bootstrap UberAdmin account.",
    )
    UBERADMIN_FULLNAME: str = Field(
        default="",
        validation_alias="NOCA_WEB_UBERADMIN_FULLNAME",
        description="Full name for the bootstrap UberAdmin account.",
    )
    UBERADMIN_EMAIL: str = Field(
        default="",
        validation_alias="NOCA_WEB_UBERADMIN_EMAIL",
        description="Email address for the bootstrap UberAdmin account.",
    )
    UBERADMIN_PASSWORD: str = Field(
        default="",
        validation_alias="NOCA_WEB_UBERADMIN_PASSWORD",
        description="Password for the bootstrap UberAdmin account.",
    )

    ENVIRONMENT: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Application environment ('development' or 'production')",
    )

    LOG_LEVEL: str | None = Field(
        default=None,
        description=(
            "Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). "
            "When unset, falls back to DEBUG in development and INFO in production."
        ),
    )

    GEOLOCATION_API_KEY: str | None = Field(
        default=None,
        description="API key for geolocation service (required for geolocation features)",
    )

    WORDLIST_FILENAME: str = Field(
        default="wordlist-pt.txt",
        description="Filename of the wordlist used for diceware password generation (resolved relative to shared/)",
    )
    PASSWORD_WORD_COUNT: int = Field(
        default=4,
        ge=1,
        description="Number of words used when generating diceware passwords (at least 1)",
    )
    MIN_PASSWORD_LENGTH: int = Field(
        default=12,
        ge=8,
        description="Minimum character length required for any password (at least 8)",
    )
    PASSWORD_UPPERCASE_REQUIRED: bool = Field(
        default=True,
        description="Whether generated passwords must include at least one uppercase letter",
    )
    PASSWORD_LOWERCASE_REQUIRED: bool = Field(
        default=True,
        description="Whether generated passwords must include at least one lowercase letter",
    )
    PASSWORD_NUMBER_REQUIRED: bool = Field(
        default=True,
        description="Whether generated passwords must include at least one number",
    )
    PASSWORD_SYMBOL_REQUIRED: bool = Field(
        default=True,
        description="Whether generated passwords must include at least one symbol",
    )

    IMAGE_AVATAR_SIZE: int = Field(
        default=64, gt=0, le=256, description="Generated avatar max size in pixels for uploaded images (64 to 256)"
    )
    IMAGE_MAX_FILE_SIZE: int = Field(
        default=5 * 1024 * 1024,
        gt=0,
        le=5 * 1024 * 1024,
        description="Maximum allowed uploaded image size in bytes (1 byte to 5 MiB)",
    )
    IMAGE_MAX_WIDTH: int = Field(
        default=2048, gt=0, le=4096, description="Maximum allowed uploaded image width in pixels (up to 4096)"
    )
    IMAGE_MAX_HEIGHT: int = Field(
        default=2048, gt=0, le=4096, description="Maximum allowed uploaded image height in pixels (up to 4096)"
    )
    IMAGE_FONT_DIR: str | None = Field(
        default=None,
        description="Optional directory containing fonts used by generated placeholders",
    )
    IMAGE_RESPONSE_CACHE_MAX_AGE: int = Field(
        default=3600, gt=0, description="Cache max-age for image responses in seconds"
    )

    PROBLEM_STATEMENT_DIR: DirectoryPath = Field(
        validation_alias="NOCA_WEB_PROBLEM_STATEMENT_DIR",
        description="Directory where problem statements are stored (must be readable and writable)",
    )
    PROBLEM_TESTCASE_DIR_ROOT: DirectoryPath = Field(
        validation_alias="NOCA_PROBLEM_TESTCASE_DIR",
        description=(
            "Root directory shared by Web, Arena, and Autojudge for problem test cases "
            "(must be readable and writable). Web problems live under the 'contest/' subdir."
        ),
    )

    VALKEY_SERVER: str | None = Field(
        default="127.0.0.1", description="Base URL of the VALKEY server (used for validating problem test cases)"
    )
    VALKEY_PORT: int = Field(default=6379, gt=0, le=65535, description="Port number of the VALKEY server (1 to 65535)")

    VALKEY_DB: int = Field(default=0, ge=0, description="Database number of the VALKEY server (0 or greater)")

    VALKEY_USER: str | None = Field(
        default=None, description="Username for authenticating with the VALKEY server (if required)"
    )

    VALKEY_PASSWORD: str | None = Field(
        default=None, description="Password for authenticating with the VALKEY server (if required)"
    )
    VALKEY_HEALTHCHECK_INTERVAL_SECONDS: int = Field(
        default=5,
        ge=1,
        le=300,
        description="How often the app pings Valkey and attempts reconnect/flush while running",
    )
    STARTUP_TIMEOUT_SECONDS: int = Field(
        default=60,
        ge=0,
        le=300,
        description="Seconds to wait for PostgreSQL and Valkey before aborting startup (0 = no wait)",
    )

    @field_validator("WEB_URL_BASE", mode="after")
    @classmethod
    def normalize_url_base(cls, v: str | None) -> str | None:
        """Strip trailing slash and validate scheme."""
        if v is None:
            return None
        v = v.strip().rstrip("/")
        if not v:
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("NOCA_WEB_URL_BASE must start with http:// or https://")
        return v

    @field_validator("FORWARDED_ALLOW_IPS", mode="after")
    @classmethod
    def normalize_forwarded_allow_ips(cls, v: str) -> str:
        """Normalize trusted proxy list for uvicorn forwarded headers support."""
        normalized_parts: list[str] = []
        for raw_part in v.split(","):
            part = raw_part.strip()
            if not part:
                continue
            if part == "*":
                normalized_parts.append(part)
                continue
            try:
                # Accept single IPs and CIDR networks.
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

    @field_validator("PROBLEM_STATEMENT_DIR", "PROBLEM_TESTCASE_DIR_ROOT", mode="after")
    @classmethod
    def check_rw_permissions(cls, v: Path) -> Path:
        # Check for Read permission
        if not os.access(v, os.R_OK):
            raise ValueError(f"Directory '{v}' is not readable.")

        # Check for Write permission
        if not os.access(v, os.W_OK):
            raise ValueError(f"Directory '{v}' is not writable.")

        return v

    @property
    def PROBLEM_TESTCASE_DIR(self) -> Path:  # noqa: N802
        """Web test-case root: the 'contest/' subdir under the shared root.

        Created on demand by the test-case writers; namespaced so the Web and
        Arena identity domains never collide under the shared mount.
        """
        return Path(self.PROBLEM_TESTCASE_DIR_ROOT) / CONTEST_TC_SUBDIR

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
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def valkey_url(self) -> str:
        auth = ""
        if self.VALKEY_USER and self.VALKEY_PASSWORD:
            auth = f"{self.VALKEY_USER}:{self.VALKEY_PASSWORD}@"
        elif self.VALKEY_PASSWORD:
            auth = f":{self.VALKEY_PASSWORD}@"
        return f"redis://{auth}{self.VALKEY_SERVER}:{self.VALKEY_PORT}/{self.VALKEY_DB}"

    @property
    def queue_pending_key(self) -> str:
        return "judge:queue:pending"

    @property
    def queue_priority_key(self) -> str:
        return "judge:queue:priority"

    @property
    def queue_inflight_key(self) -> str:
        return "judge:queue:inflight"

    @property
    def queue_inflight_times_key(self) -> str:
        return "judge:queue:inflight:times"

    @property
    def queue_results_channel(self) -> str:
        return "judge:results"

    @property
    def queue_job_hash_prefix(self) -> str:
        return "judge:job"

    @property
    def queue_profiling_key(self) -> str:
        return "judge:queue:profiling"


settings = Settings()  # type: ignore[call-arg]
