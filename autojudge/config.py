#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
All configuration for the judge worker is read from environment variables.
No secrets or environment-specific values are hardcoded.
In real world usage, probably set via a .env file shared with web module.

Usage
-----
    from autojudge.config import settings

Two settings classes are defined:

NocaSettings   — reads common infrastructure variables prefixed with NOCA_
                 (shared with every other NOCA module: database, Valkey,
                 environment, log level).
Settings       — reads judge-worker-specific variables prefixed with NOCA_JUDGE_.
                 Inherits from NocaSettings so that the shared fields are
                 available on the single `settings` object used throughout the
                 worker.

Because pydantic-settings applies the child class env_prefix to ALL fields
(including inherited ones), NocaSettings declares explicit validation_alias
values on each field so they are always resolved from the common NOCA_* names,
regardless of which prefix the inheriting class uses.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import DirectoryPath, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enumerations import Environment
from shared.services.testcase_files import ARENA_TC_SUBDIR, CONTEST_TC_SUBDIR

# ---------------------------------------------------------------------------
# Shared infrastructure settings  (NOCA_ prefix)
# ---------------------------------------------------------------------------


class NocaSettings(BaseSettings):
    """
    Shared infrastructure settings read from common NOCA_* environment variables.

    These are the same variables consumed by every other NOCA module. Declaring
    explicit validation_alias on every field ensures they are always resolved
    from NOCA_* even when this class is subclassed with a different env_prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="NOCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    DB_USER: str = Field(validation_alias="NOCA_DB_USER")
    DB_PASSWORD: str = Field(validation_alias="NOCA_DB_PASSWORD")
    DB_SERVER: str = Field(validation_alias="NOCA_DB_SERVER")
    DB_PORT: int = Field(
        default=5432,
        gt=0,
        le=65535,
        validation_alias="NOCA_DB_PORT",
        description="Port number for the database server (1 to 65535)",
    )
    DB_NAME: str = Field(validation_alias="NOCA_DB_NAME")

    ENVIRONMENT: Environment = Field(
        default=Environment.DEVELOPMENT,
        validation_alias="NOCA_ENVIRONMENT",
        description="Application environment ('development' or 'production')",
    )

    VALKEY_SERVER: str | None = Field(
        default="127.0.0.1",
        validation_alias="NOCA_VALKEY_SERVER",
        description="Base URL of the VALKEY server",
    )
    VALKEY_PORT: int = Field(
        default=6379,
        gt=0,
        le=65535,
        validation_alias="NOCA_VALKEY_PORT",
        description="Port number of the VALKEY server (1 to 65535)",
    )
    VALKEY_DB: int = Field(
        default=0,
        ge=0,
        validation_alias="NOCA_VALKEY_DB",
        description="Database number of the VALKEY server (0 or greater)",
    )
    VALKEY_USER: str | None = Field(
        default=None,
        validation_alias="NOCA_VALKEY_USER",
        description="Username for authenticating with the VALKEY server (if required)",
    )
    VALKEY_PASSWORD: str | None = Field(
        default=None,
        validation_alias="NOCA_VALKEY_PASSWORD",
        description="Password for authenticating with the VALKEY server (if required)",
    )
    VALKEY_HEALTHCHECK_INTERVAL_SECONDS: int = Field(
        default=5,
        ge=1,
        le=300,
        validation_alias="NOCA_VALKEY_HEALTHCHECK_INTERVAL_SECONDS",
        description="How often the app pings Valkey and attempts reconnect/flush while running",
    )
    WORKER_COMMAND_SECRET: str = Field(
        default="",
        validation_alias="NOCA_WORKER_COMMAND_SECRET",
        description=(
            "Shared HMAC secret for verifying pause/resume commands. When empty, the worker "
            "command loop is disabled and the worker never honors remote pause nudges."
        ),
    )
    STARTUP_TIMEOUT_SECONDS: int = Field(
        default=60,
        ge=0,
        le=300,
        validation_alias="NOCA_STARTUP_TIMEOUT_SECONDS",
        description="Seconds to wait for PostgreSQL and Valkey before aborting startup (0 = no wait)",
    )

    # ── Computed properties ──────────────────────────────────────────────────

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
    def is_production(self) -> bool:
        return self.ENVIRONMENT == Environment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == Environment.DEVELOPMENT


# ---------------------------------------------------------------------------
# Judge-worker settings  (JUDGE_ prefix)
# ---------------------------------------------------------------------------


class Settings(NocaSettings):
    """
    Judge worker settings.

    Inherits all shared infrastructure fields from NocaSettings (NOCA_*) and
    adds judge-specific fields read from NOCA_JUDGE_* environment variables.

    The model_config here sets env_prefix="NOCA_JUDGE_", which applies to fields
    declared in this class only. Inherited fields retain their explicit
    validation_alias values pointing at the common NOCA_* names, so they are
    unaffected.
    """

    model_config = SettingsConfigDict(
        env_prefix="NOCA_JUDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Worker concurrency ───────────────────────────────────────────────────
    WORKER_CONCURRENCY: int = Field(
        default=4,
        ge=1,
        le=32,
        description=(
            "Maximum number of simultaneous container executions on this host. "
            "Set to the number of available CPU cores minus one."
        ),
    )

    WORKER_ID: str = Field(
        default="",
        description=(
            "Unique identifier for this worker process. Defaults to '<hostname>:<pid>' at runtime if left empty."
        ),
    )

    PRESENCE_INTERVAL_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Seconds between Valkey worker-presence heartbeats.",
    )

    PRESENCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        description="TTL for the Valkey worker-presence live marker.",
    )

    WORKER_COMMAND_POLL_SECONDS: float = Field(
        default=3.0,
        ge=0.5,
        le=60.0,
        description="Seconds between Valkey pause/resume command-key polls.",
    )

    WORKER_COMMAND_FRESHNESS_SECONDS: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Symmetric freshness window (seconds) for accepting a signed command.",
    )

    WORKER_COMMAND_NONCE_TTL_SECONDS: int = Field(
        default=60,
        ge=2,
        le=3600,
        description="TTL for the single-use command nonce; must exceed the freshness window.",
    )

    PRE_WARM_CONTAINERS: bool = Field(
        default=True,
        description=(
            "Maintain warm container pools per language. "
            "When enabled, each language's pool is initialized on the first submission for that "
            "language (lazy warming) rather than at worker startup. "
            "Image presence is still validated eagerly at startup regardless of this setting. "
            "When false, run containers are created only on demand during acquire()."
        ),
    )

    # ── Container pool ───────────────────────────────────────────────────────
    POOL_SIZE_PER_LANGUAGE: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "Number of warm idle containers maintained per language. "
            "Increase on hosts with many concurrent submissions."
        ),
    )

    CONTAINER_MEM_LIMIT_MB: int = Field(
        default=512,
        ge=64,
        le=8192,
        description=(
            "Upper-bound memory limit for pool containers (MB). "
            "Per-problem limits are applied via container.update() before exec."
        ),
    )

    CONTAINER_PID_LIMIT: int = Field(
        default=256,
        ge=32,
        le=1024,
        description=(
            "Upper-bound PID limit for pool containers. "
            "Per-problem limits are applied via container.update() before exec."
        ),
    )

    POOL_ACQUIRE_TIMEOUT_S: float = Field(
        default=30.0,
        ge=1.0,
        description=(
            "Maximum seconds to wait for a warm container from the pool before "
            "raising a PoolExhaustedError. Should be well below the contest's "
            "maximum submission rate x judge time."
        ),
    )

    PROBLEM_TESTCASE_DIR: DirectoryPath = Field(
        validation_alias="NOCA_PROBLEM_TESTCASE_DIR",
        description=(
            "Root directory shared by Web, Arena, and Autojudge for problem test cases "
            "(must be readable). Web problems live under 'contest/', Arena under 'arena/'."
        ),
    )

    @field_validator("PROBLEM_TESTCASE_DIR", mode="after")
    @classmethod
    def check_rw_permissions(cls, v: Path) -> Path:
        if not os.access(v, os.R_OK):
            raise ValueError(f"Directory '{v}' is not readable.")
        return v

    @property
    def contest_testcase_dir(self) -> Path:
        """Root for Web (contest) problem test cases: ``<root>/contest``."""
        return Path(self.PROBLEM_TESTCASE_DIR) / CONTEST_TC_SUBDIR

    @property
    def arena_testcase_dir(self) -> Path:
        """Root for Arena problem test cases: ``<root>/arena``."""
        return Path(self.PROBLEM_TESTCASE_DIR) / ARENA_TC_SUBDIR

    # ── Queue key properties ─────────────────────────────────────────────────
    @property
    def queue_pending_key(self) -> str:
        return "judge:queue:pending"

    @property
    def queue_priority_key(self) -> str:
        return "judge:queue:priority"

    @property
    def queue_profiling_key(self) -> str:
        return "judge:queue:profiling"

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

    # ── Execution limits ─────────────────────────────────────────────────────
    ISOLATE_BINARY_PATH: str = Field(
        default="/usr/local/bin/isolate",
        description="Absolute path to the isolate binary inside run containers.",
    )

    ISOLATE_MAX_BOXES: int = Field(
        default=1000,
        ge=1,
        le=1000,
        description=(
            "Number of distinct isolate box-ids available for per-container "
            "allocation (0 .. ISOLATE_MAX_BOXES - 1). Must not exceed isolate's "
            "configured num_boxes (default 1000). Each live run container holds one "
            "box-id for its lifetime, so this bounds concurrent containers per worker."
        ),
    )

    ISOLATE_WALL_TIME_MULTIPLIER: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description=(
            "Multiplier applied to the per-problem CPU time limit to compute the inner isolate --wall-time budget."
        ),
    )

    OUTER_TIMEOUT_MULTIPLIER: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description=(
            "Multiplier applied to the computed inner isolate wall-time budget "
            "to derive the outer asyncio safety timeout."
        ),
    )

    COMPILE_TIMEOUT_S: float = Field(
        default=180.0,
        ge=5.0,
        description=(
            "Global ceiling for the compile phase wall clock, applied when the "
            "language config does not specify its own compile_timeout_s. "
            "Per-language values in languages.py always take precedence."
        ),
    )

    PROFILING_MAX_CPU_TIME_SEC: float = Field(
        default=30.0,
        ge=1.0,
        description="Hard CPU-time ceiling per profiling repetition run.",
    )

    PROFILING_MAX_WALL_TIME_SEC: float = Field(
        default=90.0,
        ge=1.0,
        description="Hard wall-time ceiling per profiling repetition run.",
    )

    PROFILING_MAX_MEMORY_MB: int = Field(
        default=2048,
        ge=64,
        le=8192,
        description="Hard memory ceiling used while profiling reference implementations.",
    )

    PROFILING_MAX_PIDS: int = Field(
        default=256,
        ge=32,
        le=2048,
        description="Hard PID ceiling used while profiling reference implementations.",
    )

    PROFILING_MAX_OUTPUT_BYTES: int = Field(
        default=64 * 1024 * 1024,
        ge=1024,
        description="Hard stdout ceiling used while profiling reference implementations.",
    )

    # ── Output limits ────────────────────────────────────────────────────────
    OUTPUT_LIMIT_BYTES: int = Field(
        default=64 * 1024 * 1024,  # 64 MB
        ge=1024,
        description=(
            "Global maximum bytes read from a container's stdout. Output that reaches "
            "this limit yields an OLE verdict without reading further."
        ),
    )

    STDOUT_EXCERPT_BYTES: int = Field(
        default=8192,
        ge=256,
        description=(
            "How many bytes of contestant stdout to persist in "
            "submission_test_result.stdout_excerpt for display in the UI."
        ),
    )

    STDERR_EXCERPT_BYTES: int = Field(
        default=4096,
        ge=256,
        description="Same as stdout_excerpt_bytes but for stderr.",
    )

    # ── Idempotency lock ─────────────────────────────────────────────────────
    LOCK_TTL_SECONDS: int = Field(
        default=660,
        ge=1,
        description=(
            "TTL (seconds) for the per-judgment Redis idempotency lock "
            "(judge:lock:<judgment_id>). Must be strictly greater than "
            "reaper_stale_threshold_minutes * 60 so that a slow-but-alive "
            "worker holds the lock past the point where the reaper would "
            "requeue the job. Default: 660 = 600 s threshold + 60 s margin."
        ),
    )

    # ── Reaper ───────────────────────────────────────────────────────────────
    REAPER_INTERVAL_S: float = Field(
        default=30.0,
        ge=5.0,
        description="How often the reaper scans for stale in-flight jobs.",
    )

    RECONCILER_INTERVAL_S: float = Field(
        default=120.0,
        ge=10.0,
        description=(
            "How often the reconciler re-scans the database for non-terminal jobs "
            "(QUEUED/DISPATCHED/JUDGING) that are missing from the Valkey queue and "
            "re-enqueues them. Recovers jobs lost between a web/arena DB commit and "
            "the follow-up Valkey enqueue without waiting for a worker restart."
        ),
    )

    REAPER_STALE_THRESHOLD_MINUTES: float = Field(
        default=5.0,
        ge=1.0,
        description=(
            "A dispatched job older than this many minutes is considered stale "
            "and will be requeued by the reaper. Must be longer than the "
            "longest possible legitimate judge run "
            "(compile_timeout + n_test_cases x time_limit)."
        ),
    )

    REAPER_MAX_REQUEUE_COUNT: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Maximum number of times the reaper will requeue a stale job before "
            "giving up and removing it from the inflight list entirely. Prevents "
            "poison-pill jobs from cycling indefinitely."
        ),
    )

    # ── Docker ───────────────────────────────────────────────────────────────
    DOCKER_BASE_URL: str = Field(
        default="unix:///var/run/docker.sock",
        description=("Docker daemon socket or TCP address. Use 'tcp://host:2376' for remote daemon with TLS."),
    )

    DOCKER_NETWORK: str = Field(
        default="none",
        description=(
            "Network mode for judge containers. Must be 'none' in production. "
            "Can be overridden to 'bridge' in local development for debugging "
            "only — never in a live contest."
        ),
    )

    DOCKER_APPARMOR_PROFILE: str = Field(
        default="",
        description=(
            "Optional AppArmor profile applied to run containers, for example "
            "'unconfined'. Empty leaves Docker's default AppArmor handling unchanged."
        ),
    )

    IMAGE_REGISTRY: str = Field(
        default="",
        description=(
            "Optional canonical registry/repository prefix for judge language images, "
            "for example 'ghcr.io/dclobato/noca'. When set, worker startup rewrites "
            "language image refs in the database according to IMAGE_NAMING before "
            "preflight."
        ),
    )

    IMAGE_NAMING: Literal["path", "flat"] = Field(
        default="path",
        description=(
            "How canonical judge image names are derived from IMAGE_REGISTRY. "
            "'path' yields '{registry}/judge-<language_id>:<compile|run>[-<tag>]' "
            "(for registries like GHCR). 'flat' yields "
            "'{registry}-judge-<language_id>:<compile|run>[-<tag>]' "
            "(for registries like Docker Hub)."
        ),
    )

    IMAGE_TAG: str = Field(
        default="",
        description=(
            "Optional canonical image tag suffix used with IMAGE_REGISTRY. "
            "When empty, tags are ':compile' and ':run'. When set to e.g. "
            "'v5.0.0', tags become ':compile-v5.0.0' and ':run-v5.0.0'."
        ),
    )

    IMAGE_PULL_POLICY: Literal["never", "missing", "always"] = Field(
        default="missing",
        description=(
            "How startup handles canonical judge images when IMAGE_REGISTRY is set: "
            "'never' only rewrites the DB, 'missing' pulls only absent images, "
            "and 'always' always pulls before startup continues."
        ),
    )

    # ── Observability ────────────────────────────────────────────────────────
    LOG_LEVEL: str | None = Field(
        default=None,
        validation_alias="NOCA_LOG_LEVEL",
        description=(
            "Logging level: DEBUG, INFO, WARNING, ERROR, or CRITICAL. "
            "Defaults to DEBUG in development and INFO in production."
        ),
    )

    HEARTBEAT_FILE: str = Field(
        default="/tmp/autojudge-heartbeat",
        description="Filesystem path touched periodically while the worker process is healthy.",
    )

    HEARTBEAT_INTERVAL_S: float = Field(
        default=10.0,
        ge=1.0,
        le=300.0,
        description="How often the worker refreshes its heartbeat file.",
    )

    HEARTBEAT_STALE_THRESHOLD_S: float = Field(
        default=30.0,
        ge=2.0,
        le=3600.0,
        description="Maximum allowed age of the heartbeat file before the container is considered unhealthy.",
    )

    # ── Metrics ──────────────────────────────────────────────────────────────
    METRICS_ENABLED: bool = Field(
        default=True,
        description=(
            "When True, expose a Prometheus /metrics HTTP endpoint. "
            "Set to False to disable the exposition server entirely."
        ),
    )

    METRICS_PORT: int = Field(
        default=9101,
        ge=1024,
        le=65535,
        description=(
            "TCP port for the Prometheus metrics HTTP endpoint. "
            "Default 9101 follows the unofficial Prometheus port convention "
            "for application-specific exporters."
        ),
    )

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str | None) -> str | None:
        if v is None:
            return None
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got '{v}'")
        return upper

    @property
    def resolved_log_level(self) -> int:
        """Effective logging level: explicit override, then environment default."""
        if self.LOG_LEVEL is not None:
            return int(getattr(logging, self.LOG_LEVEL))
        return logging.INFO if self.ENVIRONMENT == Environment.PRODUCTION else logging.DEBUG

    @field_validator("HEARTBEAT_FILE")
    @classmethod
    def validate_heartbeat_file(cls, v: str) -> str:
        path = Path(v)
        if not path.is_absolute() and not v.startswith("/"):
            raise ValueError("HEARTBEAT_FILE must be an absolute path.")
        return str(path)

    @field_validator("HEARTBEAT_STALE_THRESHOLD_S")
    @classmethod
    def validate_heartbeat_threshold(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        interval = info.data.get("HEARTBEAT_INTERVAL_S")
        if interval is not None and v <= float(interval):
            raise ValueError("HEARTBEAT_STALE_THRESHOLD_S must be greater than HEARTBEAT_INTERVAL_S.")
        return v

    @field_validator("PRESENCE_TTL_SECONDS")
    @classmethod
    def validate_presence_ttl(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        """Require the live-marker TTL to exceed the heartbeat interval."""
        interval = info.data.get("PRESENCE_INTERVAL_SECONDS")
        if interval is not None and v <= float(interval):
            raise ValueError("PRESENCE_TTL_SECONDS must be greater than PRESENCE_INTERVAL_SECONDS.")
        return v

    @field_validator("WORKER_COMMAND_NONCE_TTL_SECONDS")
    @classmethod
    def validate_command_nonce_ttl(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        """Require the nonce TTL to outlast the command freshness window."""
        freshness = info.data.get("WORKER_COMMAND_FRESHNESS_SECONDS")
        if freshness is not None and v <= float(freshness):
            raise ValueError("WORKER_COMMAND_NONCE_TTL_SECONDS must exceed WORKER_COMMAND_FRESHNESS_SECONDS.")
        return v

    @field_validator("DOCKER_APPARMOR_PROFILE", "IMAGE_REGISTRY", "IMAGE_NAMING", "IMAGE_TAG", mode="before")
    @classmethod
    def strip_optional_image_settings(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("IMAGE_REGISTRY")
    @classmethod
    def normalize_image_registry(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("IMAGE_NAMING")
    @classmethod
    def normalize_image_naming(cls, v: str) -> str:
        return v.lower()


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.

    Cached via lru_cache so the .env file and environment variables are read
    exactly once per process. In tests, call get_settings.cache_clear() before
    each test that needs a fresh configuration.
    """
    return Settings()  # type: ignore[call-arg]  # fields populated from env vars


def _load_settings_or_exit() -> Settings:
    import sys

    from pydantic import ValidationError

    try:
        return get_settings()
    except ValidationError as exc:
        lines = ["[autojudge] Configuration error — fix the following before starting the worker:"]
        for err in exc.errors():
            field = " → ".join(str(loc) for loc in err["loc"])
            lines.append(f"  • {field}: {err['msg']}")
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)


# Module-level alias for convenient import:
#   from autojudge.config import settings
settings = _load_settings_or_exit()
