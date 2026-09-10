#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
from shared.services.imageprocessing_service import MAX_IMAGE_FILE_SIZE
from shared.services.testcase_files import ARENA_TC_SUBDIR
from shared.session_keepalive import (
    derived_keepalive_seconds,
    keepalive_lands_inside_refresh_window,
    keepalive_window_error,
)


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
    HOST: str = Field(
        default="0.0.0.0",
        validation_alias="NOCA_ARENA_HOST",
        description=(
            "Bind address for the arena HTTP server. Container deployments must keep 0.0.0.0: "
            "the reverse proxy reaches the service over the container network."
        ),
    )
    PORT: int = Field(
        default=8001,
        gt=0,
        le=65535,
        validation_alias="NOCA_ARENA_PORT",
        description="TCP port for the arena HTTP server (1-65535; default 8001).",
    )
    APP_NAME: str = Field(
        default="noca-arena",
        validation_alias="NOCA_ARENA_APP_NAME",
        description=(
            "Application name; also used as the JWT issuer claim.  Differs from the web "
            "module default ('noca') so tokens issued by each server are not mutually valid."
        ),
    )
    BRAND_NAME: str = Field(
        default="NOCA Arena",
        validation_alias="NOCA_ARENA_BRAND_NAME",
        description="Public brand name shown in the UI, page titles, and email templates.",
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
    SOURCE_PORT_HEADER: str = Field(
        default="",
        description=(
            "Optional trusted reverse-proxy header carrying the original client source port. "
            "The proxy must strip client-supplied values before setting it."
        ),
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
    # Google sign-in (OpenID Connect). Off by default: with GOOGLE_OAUTH_ENABLED
    # false every /auth/google route answers 404 and no Google affordance is
    # rendered, so a deployment that has not registered an OAuth client behaves
    # exactly as it did before this feature existed. The redirect URI is derived
    # from ARENA_URL_BASE rather than configured separately, so it cannot drift
    # from the deployment's own public base URL.
    # ------------------------------------------------------------------
    GOOGLE_OAUTH_ENABLED: bool = Field(
        default=False,
        validation_alias="NOCA_ARENA_GOOGLE_OAUTH_ENABLED",
        description="Offer Google as an alternative Arena login door.",
    )
    GOOGLE_OAUTH_CLIENT_ID: str = Field(
        default="",
        validation_alias="NOCA_ARENA_GOOGLE_OAUTH_CLIENT_ID",
        description="OAuth 2.0 client ID from the Google Cloud Console.",
    )
    GOOGLE_OAUTH_CLIENT_SECRET: str = Field(
        default="",
        validation_alias="NOCA_ARENA_GOOGLE_OAUTH_CLIENT_SECRET",
        description="OAuth 2.0 client secret from the Google Cloud Console.",
    )

    # ------------------------------------------------------------------
    # Email. Arena never talks to a mail provider: every message is rendered
    # here and handed to the noca-mailer worker over Valkey, which sends or
    # logs it. NOCA_SEND_EMAIL, NOCA_EMAIL_PROVIDER, NOCA_SMTP_* and
    # NOCA_EMAIL_MBOX_LOG_DIR are the mailer's settings alone.
    # ------------------------------------------------------------------
    EMAIL_SENDER: str = Field(default="no-reply@noca.local", description="Default From address")
    EMAIL_SENDER_NAME: str | None = Field(
        default=None,
        description="Optional From display name (falls back to BRAND_NAME)",
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
    # Templates are rendered where the email is composed, so this belongs to the
    # `webarena` layer rather than to `email`: the mailer receives finished
    # messages and has no use for an override mount.
    EMAIL_TEMPLATE_OVERRIDE_DIR: DirectoryPath | None = Field(
        default=None,
        description=(
            "Optional host-managed directory of email template overrides, read from its "
            "'arena/' subdirectory. Empty disables overrides and every email renders from "
            "the packaged defaults. Every file in the namespace is validated at startup and "
            "Arena refuses to start on any error."
        ),
    )
    CLASS_REGISTRATION_RETRY_SECONDS: int = Field(
        default=86400,
        ge=0,
        validation_alias="NOCA_ARENA_CLASS_REGISTRATION_RETRY_SECONDS",
        description=(
            "Seconds a student must wait after a denied class registration request before "
            "requesting the same class again; 0 disables the wait."
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
    IPQUALITYSCORE_APIKEY: str | None = Field(
        default=None,
        description="API key for IPQualityScore email validation and IP reputation checks (optional).",
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
    PUBLIC_PROBLEM_PACK_PATH: Path | None = Field(
        default=None,
        validation_alias="NOCA_ARENA_PUBLIC_PROBLEM_PACK_PATH",
        description=(
            "Cache directory for the per-problem public export and sample-case ZIPs served to "
            "every logged-in user. Required in production: Arena refuses to start without it. "
            "Created at startup when set; each artifact is built once and reused until the "
            "problem changes."
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
        default=2 * 1024 * 1024,
        gt=0,
        le=MAX_IMAGE_FILE_SIZE,
        description="Maximum allowed uploaded image size in bytes (up to 5 MiB).",
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
    AUTH_RATE_LIMIT_2FA_IP_DISTINCT_ACCOUNTS: int = Field(
        default=3,
        ge=1,
        description=(
            "Distinct accounts an address must fail against, on top of the raw IP ceiling, before the "
            "login 2FA step locks that address. Reaching the step needs a valid password, so failures "
            "spanning one account are someone fumbling a code; spraying spans many. 1 restores the "
            "plain per-IP count."
        ),
    )
    USER_READ_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_ARENA_USER_READ_RATE_LIMIT_ENABLED",
        description="Enable the loose per-user ceiling on authenticated reads and polled partials.",
    )
    USER_READ_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=300,
        ge=1,
        validation_alias="NOCA_ARENA_USER_READ_RATE_LIMIT_MAX_REQUESTS",
        description=(
            "Requests one user may make to the rate-limited read routers in each window. Generous by "
            "design: legitimate clients poll these every few seconds and each call costs a bounded amount."
        ),
    )
    USER_READ_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        validation_alias="NOCA_ARENA_USER_READ_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for the per-user read ceiling.",
    )

    # ------------------------------------------------------------------
    # Per-problem export limiting (/problems/{n}/export, /problems/{n}/sample-testcases.zip)
    # ------------------------------------------------------------------
    PROBLEM_EXPORT_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_ARENA_PROBLEM_EXPORT_RATE_LIMIT_ENABLED",
        description="Enable the tight per-user budget on per-problem package and sample-ZIP downloads.",
    )
    PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=10,
        ge=1,
        validation_alias="NOCA_ARENA_PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS",
        description=(
            "Problem package and sample-ZIP downloads accepted per user in each fixed window, cached or "
            "not. Tight by design: building one is expensive and a user downloads a given problem once."
        ),
    )
    PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_ARENA_PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for per-user problem downloads.",
    )

    ADMIN_EXPORT_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_ARENA_ADMIN_EXPORT_RATE_LIMIT_ENABLED",
        description="Enable the per-user budget on Arena admin exports (full problem package, security-events CSV).",
    )
    ADMIN_EXPORT_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=20,
        ge=1,
        validation_alias="NOCA_ARENA_ADMIN_EXPORT_RATE_LIMIT_MAX_REQUESTS",
        description="Arena admin export downloads accepted per user in each fixed window; the next gets 429.",
    )
    ADMIN_EXPORT_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_ARENA_ADMIN_EXPORT_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for per-user Arena admin exports.",
    )

    TEACHER_REPORT_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_ARENA_TEACHER_REPORT_RATE_LIMIT_ENABLED",
        description="Enable the per-user budget on the teacher problem-set report pages and CSV.",
    )
    TEACHER_REPORT_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=60,
        ge=1,
        validation_alias="NOCA_ARENA_TEACHER_REPORT_RATE_LIMIT_MAX_REQUESTS",
        description="Teacher report page loads and CSV downloads accepted per user in each fixed window.",
    )
    TEACHER_REPORT_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_ARENA_TEACHER_REPORT_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for per-user teacher reports.",
    )

    SIGNUP_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=5,
        ge=1,
        validation_alias="NOCA_ARENA_SIGNUP_RATE_LIMIT_MAX_REQUESTS",
        description=(
            "Maximum signup attempts per client IP in each fixed window, counted once a submission "
            "passes form validation and reaches the account lookup, reputation calls, insert and email."
        ),
    )
    SIGNUP_REQUEST_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=60,
        ge=1,
        validation_alias="NOCA_ARENA_SIGNUP_REQUEST_RATE_LIMIT_MAX_REQUESTS",
        description=(
            "Maximum POST /auth/signup requests per client IP in each fixed window, counted before any "
            "validation. A flood guard sized well above the attempt budget so form fumbles never reach it."
        ),
    )
    SIGNUP_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=3600,
        ge=1,
        validation_alias="NOCA_ARENA_SIGNUP_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for per-IP signup rate limiting.",
    )
    AI_REVIEW_RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_ARENA_AI_REVIEW_RATE_LIMIT_ENABLED",
        description="Cap AI review requests per user (POST /submissions/{id}/request-ai-review).",
    )
    AI_REVIEW_RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=30,
        ge=1,
        validation_alias="NOCA_ARENA_AI_REVIEW_RATE_LIMIT_MAX_REQUESTS",
        description="AI review requests accepted per user in each fixed window, whatever the outcome.",
    )
    AI_REVIEW_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_ARENA_AI_REVIEW_RATE_LIMIT_WINDOW_SECONDS",
        description="Fixed-window length in seconds for per-user AI review request limiting.",
    )
    REJUDGE_COOLDOWN_SECONDS: int = Field(
        default=300,
        ge=0,
        validation_alias="NOCA_ARENA_REJUDGE_COOLDOWN_SECONDS",
        description=(
            "Seconds after a rejudge-all during which another rejudge-all of the same problem "
            "is refused; 0 disables the cooldown."
        ),
    )
    USERNAME_CHANGE_COOLDOWN_DAYS: int = Field(
        default=30,
        ge=0,
        validation_alias="NOCA_ARENA_USERNAME_CHANGE_COOLDOWN_DAYS",
        description=(
            "Days a user must wait between username changes; 0 disables the cooldown. The "
            "username is the pseudonym an age-shielded user is published under, so unlimited "
            "churn would let an observer correlate old and new handles across the ranking."
        ),
    )
    GEOCODE_RATE_LIMIT_USER_MAX_REQUESTS: int = Field(
        default=5,
        ge=1,
        validation_alias="NOCA_ARENA_GEOCODE_RATE_LIMIT_USER_MAX_REQUESTS",
        description="Location detections accepted per user in each window, cache hits included.",
    )
    GEOCODE_RATE_LIMIT_USER_WINDOW_SECONDS: int = Field(
        default=3600,
        ge=1,
        validation_alias="NOCA_ARENA_GEOCODE_RATE_LIMIT_USER_WINDOW_SECONDS",
        description="Fixed-window length in seconds for per-user location detection limiting.",
    )
    GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS: int = Field(
        default=30,
        ge=1,
        validation_alias="NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS",
        description="Upstream geocoder calls the whole deployment may make in each global window.",
    )
    GEOCODE_RATE_LIMIT_GLOBAL_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        validation_alias="NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_WINDOW_SECONDS",
        description="Fixed-window length in seconds for the deployment-wide geocoder budget.",
    )
    GEOCODE_RATE_LIMIT_GLOBAL_MIN_INTERVAL_SECONDS: int = Field(
        default=1,
        ge=0,
        validation_alias="NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_MIN_INTERVAL_SECONDS",
        description=(
            "Minimum seconds between two upstream geocoder calls across the deployment. "
            "0 disables pacing, which is only appropriate for a self-hosted provider; the "
            "windowed budget still applies and cannot be switched off."
        ),
    )
    GEOCODE_CACHE_TTL_SECONDS: int = Field(
        default=86400,
        ge=1,
        validation_alias="NOCA_ARENA_GEOCODE_CACHE_TTL_SECONDS",
        description="How long a reverse-geocoded 0.001-degree cell stays cached in Valkey.",
    )
    # ------------------------------------------------------------------
    # SSE connection limits (concurrent streams per IP / per user)
    # ------------------------------------------------------------------
    SSE_LIMIT_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_ARENA_SSE_LIMIT_ENABLED",
        description="Cap the number of SSE streams one client may hold open at once.",
    )
    SSE_MAX_PER_IP: int = Field(
        default=200,
        ge=1,
        validation_alias="NOCA_ARENA_SSE_MAX_PER_IP",
        description="Concurrent SSE streams allowed per client IP across this module's event routes.",
    )
    SSE_MAX_PER_USER: int = Field(
        default=10,
        ge=1,
        validation_alias="NOCA_ARENA_SSE_MAX_PER_USER",
        description="Concurrent SSE streams allowed per authenticated user, across IPs.",
    )
    SSE_CONNECTION_TTL_SECONDS: int = Field(
        default=600,
        ge=1,
        validation_alias="NOCA_ARENA_SSE_CONNECTION_TTL_SECONDS",
        description="Lease lifetime of one held SSE slot in Valkey; renewed while the stream is open.",
    )
    SSE_TRUSTED_CIDRS: str = Field(
        default="127.0.0.0/8,::1/128",
        validation_alias="NOCA_ARENA_SSE_TRUSTED_CIDRS",
        description="Comma-separated CIDRs exempt from the SSE connection caps.",
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
        validation_alias="NOCA_ARENA_SECURITY_EVENTS_REAPER_INTERVAL_SECONDS",
        description="Polling interval for the Arena security-events retention reaper in seconds (1 hour to 7 days).",
    )
    ARENA_LIVE_FEED_LIMIT: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of finalized submissions returned by the public Arena live feed.",
    )

    # ------------------------------------------------------------------
    # Ranking medals (dashboard leaderboard and /ranking pages)
    # ------------------------------------------------------------------
    ARENA_RANKING_MEDAL_GOLD_CUTOFF: int = Field(
        default=1,
        ge=0,
        le=1000,
        description="Last ranking position awarded a gold medal (0 disables the gold band).",
    )
    ARENA_RANKING_MEDAL_SILVER_CUTOFF: int = Field(
        default=2,
        ge=0,
        le=1000,
        description="Last ranking position awarded a silver medal (0 disables the silver band).",
    )
    ARENA_RANKING_MEDAL_BRONZE_CUTOFF: int = Field(
        default=3,
        ge=0,
        le=1000,
        description="Last ranking position awarded a bronze medal (0 disables the bronze band).",
    )

    # ------------------------------------------------------------------
    # Online presence (green-dot indicator on user avatars)
    # ------------------------------------------------------------------
    PRESENCE_ENABLED: bool = Field(
        default=True,
        validation_alias="NOCA_ARENA_PRESENCE_ENABLED",
        description="Enable the online-presence indicator on Arena user avatars.",
    )
    PRESENCE_TTL_SECONDS: int = Field(
        default=60,
        ge=10,
        le=600,
        validation_alias="NOCA_ARENA_PRESENCE_TTL_SECONDS",
        description=(
            "Seconds a user stays 'online' after their last heartbeat or page view. "
            "Must be greater than NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS."
        ),
    )
    PRESENCE_HEARTBEAT_SECONDS: int = Field(
        default=30,
        ge=5,
        le=300,
        validation_alias="NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS",
        description=(
            "Client heartbeat / dot-refresh interval in seconds. Must be smaller than NOCA_ARENA_PRESENCE_TTL_SECONDS."
        ),
    )

    # ------------------------------------------------------------------
    # Health monitor
    # ------------------------------------------------------------------
    HEALTHMON_URL: str = Field(
        default="",
        validation_alias="NOCA_HEALTHMON_URL",
        description=(
            "Public URL of the health monitor uptime dashboard (e.g. https://status.example.com). "
            "Shown as the footer 'Status' link; the link is hidden when empty."
        ),
    )

    # ------------------------------------------------------------------
    # Worker presence (healthmonitor up/down probing of this Arena replica)
    # ------------------------------------------------------------------
    WORKER_ID: str = Field(
        default="",
        validation_alias="NOCA_ARENA_WORKER_ID",
        description="Stable worker ID; defaults to '<fqdn>:<pid>' when empty.",
    )
    WORKER_PRESENCE_INTERVAL_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        validation_alias="NOCA_ARENA_WORKER_PRESENCE_INTERVAL_SECONDS",
        description="Seconds between Valkey worker-presence heartbeats.",
    )
    WORKER_PRESENCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        validation_alias="NOCA_ARENA_WORKER_PRESENCE_TTL_SECONDS",
        description=(
            "TTL for the Valkey worker-presence live marker. "
            "Must be greater than NOCA_ARENA_WORKER_PRESENCE_INTERVAL_SECONDS."
        ),
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

    @field_validator("SSE_TRUSTED_CIDRS", mode="after")
    @classmethod
    def normalize_sse_trusted_cidrs(cls, v: str) -> str:
        """Normalize trusted CIDRs for the SSE connection-cap bypass."""
        normalized_parts: list[str] = []
        for raw_part in v.split(","):
            part = raw_part.strip()
            if not part:
                continue
            try:
                ip_network(part, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"NOCA_ARENA_SSE_TRUSTED_CIDRS must contain valid CIDRs only. Invalid value: '{part}'"
                ) from exc
            normalized_parts.append(part)
        normalized = ",".join(normalized_parts)
        if not normalized:
            raise ValueError("NOCA_ARENA_SSE_TRUSTED_CIDRS cannot be empty.")
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

    @field_validator("EMAIL_TEMPLATE_OVERRIDE_DIR", mode="before")
    @classmethod
    def blank_email_template_override_dir_disables(cls, v: object) -> object:
        """Read an empty template value as "overrides disabled", not as the cwd.

        The layered templates ship every variable present and empty, and an empty
        string would otherwise validate as ``Path(".")`` -- a directory that
        exists, so nothing would complain while the working directory silently
        became the override root.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("PUBLIC_PROBLEM_PACK_PATH", mode="after")
    @classmethod
    def normalize_problem_pack_path(cls, v: Path | None) -> Path | None:
        """Require an absolute path; an existing directory must be writable.

        Unlike the storage directories, the cache directory is allowed not to
        exist yet: Arena creates it at startup. Only a path that already exists
        must be a readable/writable directory.
        """
        if v is None:
            return None
        if not v.is_absolute():
            raise ValueError("NOCA_ARENA_PUBLIC_PROBLEM_PACK_PATH must be an absolute path.")
        if v.exists():
            if not v.is_dir():
                raise ValueError(f"NOCA_ARENA_PUBLIC_PROBLEM_PACK_PATH '{v}' is not a directory.")
            if not os.access(v, os.R_OK) or not os.access(v, os.W_OK):
                raise ValueError(f"Directory '{v}' must be readable and writable.")
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

    @property
    def session_keepalive_seconds(self) -> int:
        """Effective browser heartbeat cadence, in seconds.

        One timer serves two purposes. With presence enabled it must run at the
        presence cadence, since that is what keeps the green dot lit. With
        presence disabled it still has to run, because the same request is what
        rotates the sliding auth cookie for someone reading or typing without
        navigating, and then only needs to land inside the refresh window.
        """
        if self.PRESENCE_ENABLED:
            return int(self.PRESENCE_HEARTBEAT_SECONDS)
        return derived_keepalive_seconds(self.JWT_EXPIRE_SECONDS)

    @model_validator(mode="after")
    def validate_google_oauth_settings(self) -> Settings:
        """Refuse an enabled Google login with no client credentials.

        Failing at startup is the point: the alternative is a login page that
        offers a Google button which cannot work, discovered only when a user
        clicks it.
        """
        if self.GOOGLE_OAUTH_ENABLED and not (
            self.GOOGLE_OAUTH_CLIENT_ID.strip() and self.GOOGLE_OAUTH_CLIENT_SECRET.strip()
        ):
            raise ValueError(
                "NOCA_ARENA_GOOGLE_OAUTH_ENABLED requires both "
                "NOCA_ARENA_GOOGLE_OAUTH_CLIENT_ID and NOCA_ARENA_GOOGLE_OAUTH_CLIENT_SECRET."
            )
        return self

    @model_validator(mode="after")
    def validate_presence_settings(self) -> Settings:
        """Ensure the heartbeat interval stays below the presence TTL."""
        if self.PRESENCE_HEARTBEAT_SECONDS >= self.PRESENCE_TTL_SECONDS:
            raise ValueError(
                "NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS must be smaller than "
                "NOCA_ARENA_PRESENCE_TTL_SECONDS so a still-active user never expires."
            )
        if self.WORKER_PRESENCE_TTL_SECONDS <= self.WORKER_PRESENCE_INTERVAL_SECONDS:
            raise ValueError(
                "NOCA_ARENA_WORKER_PRESENCE_TTL_SECONDS must be greater than "
                "NOCA_ARENA_WORKER_PRESENCE_INTERVAL_SECONDS."
            )
        return self

    @model_validator(mode="after")
    def validate_session_keepalive(self) -> Settings:
        """Refuse a heartbeat cadence that would never rotate the session.

        The cadence is load-bearing for how long a session lives: a page left
        open makes no other request, so a ping slower than the refresh window
        rotates nothing and the session dies mid-edit. With presence enabled the
        cadence is an operator setting bounded only against the presence TTL, so
        nothing else relates it to the token lifetime -- a short
        NOCA_JWT_EXPIRE_SECONDS and a slow NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS
        are each individually valid and together silently broken.
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
                    cadence_setting=("NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS" if self.PRESENCE_ENABLED else None),
                )
            )
        return self

    @model_validator(mode="after")
    def validate_ranking_medal_cutoffs(self) -> Settings:
        """Require the enabled medal cutoffs to grow from gold to bronze.

        A cutoff of 0 disables its band and is skipped by the ordering check, so
        'no gold, silver up to 2, bronze up to 3' (0/2/3) is a valid setup.
        """
        enabled = [
            value
            for value in (
                self.ARENA_RANKING_MEDAL_GOLD_CUTOFF,
                self.ARENA_RANKING_MEDAL_SILVER_CUTOFF,
                self.ARENA_RANKING_MEDAL_BRONZE_CUTOFF,
            )
            if value > 0
        ]
        if any(later < earlier for earlier, later in zip(enabled, enabled[1:], strict=False)):
            raise ValueError(
                "NOCA_ARENA_RANKING_MEDAL_GOLD_CUTOFF, NOCA_ARENA_RANKING_MEDAL_SILVER_CUTOFF and "
                "NOCA_ARENA_RANKING_MEDAL_BRONZE_CUTOFF must not decrease (ignoring bands disabled with 0)."
            )
        return self

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
