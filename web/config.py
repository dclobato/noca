#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import logging
import os
from ipaddress import ip_address, ip_network
from pathlib import Path

from pydantic import DirectoryPath, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment
from shared.services.imageprocessing_service import MAX_IMAGE_FILE_SIZE
from shared.services.testcase_files import CONTEST_TC_SUBDIR
from shared.session_keepalive import (
    derived_keepalive_seconds,
    keepalive_lands_inside_refresh_window,
    keepalive_window_error,
)
from web.audio_upload_limits import DEFAULT_AUDIO_MAX_FILE_SIZE, MAX_AUDIO_FILE_SIZE


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

    HOST: str = Field(
        default="0.0.0.0",
        validation_alias="NOCA_WEB_HOST",
        description=(
            "Bind address for the web HTTP server. Container deployments must keep 0.0.0.0: "
            "the reverse proxy reaches the service over the container network."
        ),
    )
    PORT: int = Field(
        default=8000,
        gt=0,
        le=65535,
        validation_alias="NOCA_WEB_PORT",
        description="TCP port for the web HTTP server (1-65535; default 8000).",
    )

    APP_NAME: str = Field(default="noca", validation_alias="NOCA_WEB_APP_NAME")
    BRAND_NAME: str = Field(
        default="NOCA Contest",
        validation_alias="NOCA_WEB_BRAND_NAME",
        description="Public brand name shown in the UI, page titles, and email templates.",
    )
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
    SOURCE_PORT_HEADER: str = Field(
        default="",
        description=(
            "Optional trusted reverse-proxy header carrying the original client source port. "
            "The proxy must strip client-supplied values before setting it."
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
    # Outbound email. Web never talks to a mail provider: every message is rendered
    # here and handed to the noca-mailer worker over Valkey, which sends or logs it.
    # NOCA_SEND_EMAIL, NOCA_EMAIL_PROVIDER, NOCA_SMTP_* and NOCA_EMAIL_MBOX_LOG_DIR are
    # the mailer's settings alone.
    EMAIL_SENDER: str = Field(
        default="no-reply@noca.local",
        description="Default sender email used by EmailService",
    )
    EMAIL_SENDER_NAME: str | None = Field(
        default=None,
        description="Optional default sender display name (falls back to BRAND_NAME)",
    )
    EMAIL_QUEUE_JOB_TTL_SECONDS: int = Field(
        default=3600,
        ge=60,
        description="Seconds a queued email may wait for the mailer before it is dropped unsent.",
    )
    EMAIL_BUDGET_ENABLED: bool = Field(default=True, description="Enforce the per-actor outbound-email budget.")
    EMAIL_BUDGET_WINDOW_SECONDS: int = Field(
        default=600, ge=1, description="Fixed-window length in seconds for the per-actor email budget."
    )
    EMAIL_BUDGET_USER_MAX: int = Field(
        default=20,
        ge=0,
        description="Emails one ordinary user or anonymous IP may trigger per window; 0 disables that tier.",
    )
    EMAIL_BUDGET_ADMIN_MAX: int = Field(
        default=200,
        ge=0,
        description="Emails one admin actor may trigger per window; 0 disables that tier.",
    )
    HEALTHMON_URL: str = Field(
        default="",
        validation_alias="NOCA_HEALTHMON_URL",
        description=(
            "Public URL of the health monitor uptime dashboard (e.g. https://status.example.com). "
            "Shown as the footer 'Status' link; the link is hidden when empty."
        ),
    )
    WORKER_ID: str = Field(
        default="",
        validation_alias="NOCA_WEB_WORKER_ID",
        description="Stable worker ID; defaults to '<fqdn>:<pid>' when empty.",
    )
    WORKER_PRESENCE_INTERVAL_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        validation_alias="NOCA_WEB_WORKER_PRESENCE_INTERVAL_SECONDS",
        description="Seconds between Valkey worker-presence heartbeats.",
    )
    WORKER_PRESENCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        validation_alias="NOCA_WEB_WORKER_PRESENCE_TTL_SECONDS",
        description=(
            "TTL for the Valkey worker-presence live marker. "
            "Must be greater than NOCA_WEB_WORKER_PRESENCE_INTERVAL_SECONDS."
        ),
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
    SECURITY_EVENTS_RETENTION_DAYS: int = Field(
        default=180,
        ge=0,
        le=3650,
        validation_alias="NOCA_SECURITY_EVENTS_RETENTION_DAYS",
        description="Days to retain security-event rows before the reaper deletes them (0 disables cleanup).",
    )
    SECURITY_EVENTS_REAPER_INTERVAL_SECONDS: int = Field(
        default=86400,
        ge=3600,
        le=604800,
        validation_alias="NOCA_WEB_SECURITY_EVENTS_REAPER_INTERVAL_SECONDS",
        description="Polling interval for the security-events retention reaper in seconds (1 hour to 7 days).",
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
    # ------------------------------------------------------------------
    # Team write throttles (SOS tasks, print requests, clarifications)
    # ------------------------------------------------------------------
    TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_WEB_TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS",
        description="Rolling window in seconds for per-team SOS and print task budgets.",
    )
    TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS: int = Field(
        default=5,
        ge=0,
        validation_alias="NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS",
        description="SOS tasks a team may create within the window; 0 disables this limit.",
    )
    TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS: int = Field(
        default=3,
        ge=0,
        validation_alias="NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS",
        description="Unfinished SOS tasks a team may hold at once; 0 disables this limit.",
    )
    TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS: int = Field(
        default=10,
        ge=0,
        validation_alias="NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS",
        description="Print tasks a team may create within the window; 0 disables this limit.",
    )
    CLARIFICATION_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_WEB_CLARIFICATION_RATE_LIMIT_WINDOW_SECONDS",
        description="Rolling window in seconds for per-team clarification requests.",
    )
    CLARIFICATION_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=5,
        ge=0,
        validation_alias="NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_REQUESTS",
        description="Clarifications a team may ask within the window; 0 disables this limit.",
    )
    CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED: int = Field(
        default=3,
        ge=0,
        validation_alias="NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED",
        description="Unanswered clarifications a team may hold at once; 0 disables this limit.",
    )
    # ------------------------------------------------------------------
    # Mass rejudge cooldown (limit-change batch "Rejudge All Pending")
    # ------------------------------------------------------------------
    REJUDGE_COOLDOWN_SECONDS: int = Field(
        default=300,
        ge=0,
        validation_alias="NOCA_WEB_REJUDGE_COOLDOWN_SECONDS",
        description=(
            "Seconds after a batch-wide rejudge during which another batch-wide rejudge of the "
            "same problem is refused; 0 disables the cooldown."
        ),
    )
    # ------------------------------------------------------------------
    # SSE connection limits (concurrent streams per IP / per user)
    # ------------------------------------------------------------------
    SSE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_WEB_SSE_LIMIT_ENABLED",
        description="Cap the number of SSE streams one client may hold open at once.",
    )
    SSE_MAX_PER_IP: int = Field(
        default=200,
        ge=1,
        validation_alias="NOCA_WEB_SSE_MAX_PER_IP",
        description="Concurrent SSE streams allowed per client IP across this module's event routes.",
    )
    SSE_MAX_PER_USER: int = Field(
        default=10,
        ge=1,
        validation_alias="NOCA_WEB_SSE_MAX_PER_USER",
        description="Concurrent SSE streams allowed per authenticated user, across IPs.",
    )
    SSE_CONNECTION_TTL_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_WEB_SSE_CONNECTION_TTL_SECONDS",
        description="Lease lifetime of one held SSE slot in Valkey; renewed while the stream is open.",
    )
    SSE_TRUSTED_CIDRS: str = Field(
        default="127.0.0.0/8,::1/128",
        validation_alias="NOCA_WEB_SSE_TRUSTED_CIDRS",
        description="Comma-separated CIDRs exempt from the SSE connection caps.",
    )
    # ------------------------------------------------------------------
    # Public read rate limiting (/problem-set/{slug}.zip, /c/{slug}/live/feed.json)
    # ------------------------------------------------------------------
    PUBLIC_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_WEB_PUBLIC_RATE_LIMIT_ENABLED",
        description="Enable per-IP rate limiting of the anonymous problem-set download and live-feed snapshot.",
    )
    PUBLIC_RATE_LIMIT_TRUSTED_CIDRS: str = Field(
        default="127.0.0.0/8,::1/128",
        validation_alias="NOCA_WEB_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS",
        description="Comma-separated CIDRs exempt from both public read limits.",
    )
    PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS: int = Field(
        default=10,
        ge=1,
        validation_alias="NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS",
        description="Problem-set archive downloads accepted per client IP in each fixed window.",
    )
    PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS",
        description="Fixed-window length in seconds for problem-set downloads.",
    )
    PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS: int = Field(
        default=120,
        ge=1,
        validation_alias="NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS",
        description="Live-feed snapshot requests accepted per client IP in each fixed window.",
    )
    PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        validation_alias="NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS",
        description="Fixed-window length in seconds for live-feed snapshot requests.",
    )
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
    USER_READ_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_WEB_USER_READ_RATE_LIMIT_ENABLED",
        description="Enable the loose per-actor ceiling on authenticated reads and polled partials.",
    )
    USER_READ_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=300,
        ge=1,
        validation_alias="NOCA_WEB_USER_READ_RATE_LIMIT_MAX_REQUESTS",
        description=(
            "Requests one actor may make to the rate-limited read routers in each window. Generous by "
            "design: legitimate clients poll these every few seconds and each call costs a bounded amount."
        ),
    )
    USER_READ_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        validation_alias="NOCA_WEB_USER_READ_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for the per-actor read ceiling.",
    )

    # ------------------------------------------------------------------
    # Per-problem export limiting (/c/{slug}/problems/{label}/export)
    # ------------------------------------------------------------------
    PROBLEM_EXPORT_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_ENABLED",
        description="Enable the tight per-actor budget on per-problem package downloads.",
    )
    PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=10,
        ge=1,
        validation_alias="NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS",
        description=(
            "Problem package downloads accepted per actor in each fixed window, cached or not. Tight by "
            "design: building one package is expensive and a contestant downloads a given problem once."
        ),
    )
    PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for per-actor problem package downloads.",
    )

    HEALTH_RATE_LIMIT_TRUSTED_CIDRS: str = Field(
        default="127.0.0.0/8,::1/128",
        description="Comma-separated CIDRs exempt from /health rate limiting.",
    )
    SECURITY_HEADERS_ENABLED: bool = Field(
        default=True,
        description="Enable shared browser security headers.",
    )
    CSP_REPORT_ONLY: bool = Field(
        default=True,
        description="Send Content-Security-Policy-Report-Only instead of enforcing CSP.",
    )
    AUTH_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        description="Enable Valkey-backed authentication throttling.",
    )
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=900,
        ge=1,
        description="Failure-count window for auth throttling in seconds.",
    )
    AUTH_RATE_LIMIT_IP_MAX_FAILURES: int = Field(
        default=20,
        ge=1,
        description="Maximum auth failures per client IP before lockout.",
    )
    AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES: int = Field(
        default=5,
        ge=1,
        description="Maximum auth failures per account identifier before lockout.",
    )
    AUTH_RATE_LIMIT_LOCKOUT_SECONDS: int = Field(
        default=900,
        ge=1,
        description="Auth lockout duration in seconds.",
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

    IPQUALITYSCORE_APIKEY: str | None = Field(
        default=None,
        description="API key for IPQualityScore email validation and IP reputation checks (optional).",
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
        default=2 * 1024 * 1024,
        gt=0,
        le=MAX_IMAGE_FILE_SIZE,
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
    AUDIO_MAX_FILE_SIZE: int = Field(
        default=DEFAULT_AUDIO_MAX_FILE_SIZE,
        gt=0,
        le=MAX_AUDIO_FILE_SIZE,
        description="Maximum allowed uploaded audio size in bytes (1 byte to 5 MiB)",
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
    PUBLIC_PROBLEM_PACK_PATH: Path | None = Field(
        default=None,
        validation_alias="NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH",
        description=(
            "Optional cache directory for the public post-contest problem-set archives. "
            "Created at startup when set; each released contest's ZIP is built once, stored "
            "here with a SHA-256 sidecar, and reused until the sidecar digest no longer "
            "matches the file. When unset, archives are rebuilt on every download."
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

    @field_validator("SSE_TRUSTED_CIDRS", "PUBLIC_RATE_LIMIT_TRUSTED_CIDRS", mode="after")
    @classmethod
    def normalize_sse_trusted_cidrs(cls, v: str, info: ValidationInfo) -> str:
        """Normalize trusted CIDRs for the SSE connection-cap and public-read bypasses."""
        env_name = (
            "NOCA_WEB_SSE_TRUSTED_CIDRS"
            if info.field_name == "SSE_TRUSTED_CIDRS"
            else "NOCA_WEB_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS"
        )
        normalized_parts: list[str] = []
        for raw_part in v.split(","):
            part = raw_part.strip()
            if not part:
                continue
            try:
                ip_network(part, strict=False)
            except ValueError as exc:
                raise ValueError(f"{env_name} must contain valid CIDRs only. Invalid value: '{part}'") from exc
            normalized_parts.append(part)
        normalized = ",".join(normalized_parts)
        if not normalized:
            raise ValueError(f"{env_name} cannot be empty.")
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

    @field_validator("PUBLIC_PROBLEM_PACK_PATH", mode="after")
    @classmethod
    def normalize_problem_pack_path(cls, v: Path | None) -> Path | None:
        """Require an absolute path; an existing directory must be writable.

        Unlike the storage directories, the cache directory is allowed not to
        exist yet: Web creates it at startup. Only a path that already exists
        must be a readable/writable directory.
        """
        if v is None:
            return None
        if not v.is_absolute():
            raise ValueError("NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH must be an absolute path.")
        if v.exists():
            if not v.is_dir():
                raise ValueError(f"NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH '{v}' is not a directory.")
            if not os.access(v, os.R_OK) or not os.access(v, os.W_OK):
                raise ValueError(f"Directory '{v}' must be readable and writable.")
        return v

    @model_validator(mode="after")
    def validate_email_settings(self) -> Settings:
        """Validate the sender identity every queued email carries."""
        if not self.EMAIL_SENDER.strip():
            raise ValueError("NOCA_EMAIL_SENDER cannot be empty.")
        return self

    @model_validator(mode="after")
    def validate_security_settings(self) -> Settings:
        """Validate production security settings."""
        if self.ENVIRONMENT == Environment.PRODUCTION and not self.COOKIE_SECURE:
            raise ValueError("NOCA_COOKIE_SECURE must be true when NOCA_ENVIRONMENT=production.")
        return self

    @property
    def session_keepalive_seconds(self) -> int:
        """Effective browser heartbeat cadence, in seconds.

        Web has no presence feature, so the keepalive is all this timer does and
        the cadence is derived from the token lifetime rather than configured.
        """
        return derived_keepalive_seconds(self.JWT_EXPIRE_SECONDS)

    @model_validator(mode="after")
    def validate_session_keepalive(self) -> Settings:
        """Refuse a token lifetime too short for the keepalive to rotate it.

        The cadence is derived, so it tracks the lifetime on its own -- except at
        the floor under it, which bounds request volume and stops tracking below
        roughly two minutes of lifetime. Past that point the ping arrives outside
        the refresh window, rotates nothing, and open pages are logged out
        mid-edit. Refuse rather than clamp: a clamped cadence is not the one the
        operator configured, and silence is the failure mode being fixed here.
        """
        keepalive_seconds = self.session_keepalive_seconds
        if not keepalive_lands_inside_refresh_window(
            keepalive_seconds=keepalive_seconds,
            token_lifetime_seconds=self.JWT_EXPIRE_SECONDS,
        ):
            raise ValueError(
                keepalive_window_error(
                    keepalive_seconds=keepalive_seconds,
                    token_lifetime_seconds=self.JWT_EXPIRE_SECONDS,
                    cadence_setting=None,
                )
            )
        return self

    @model_validator(mode="after")
    def validate_worker_presence_settings(self) -> Settings:
        """Require the live-marker TTL to exceed the heartbeat interval."""
        if self.WORKER_PRESENCE_TTL_SECONDS <= self.WORKER_PRESENCE_INTERVAL_SECONDS:
            raise ValueError(
                "NOCA_WEB_WORKER_PRESENCE_TTL_SECONDS must be greater than NOCA_WEB_WORKER_PRESENCE_INTERVAL_SECONDS."
            )
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
