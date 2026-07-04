#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena FastAPI application entry point.

Wires up the seven core services at startup:
  1. SecretsManager   — transparent OTP-secret encryption via EncryptedString
  2. Database pool    — async SQLAlchemy engine + session factory
  3. ValkeyRuntime    — connection management and health-check loop
  4. RevocationStore  — Valkey-backed JWT revocation
  5. JWTService       — token issuance and validation
  6. EmailService     — transactional email delivery
  7. GeolocationIP    — IP-to-location lookup for login history (optional)
"""

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from jinja2 import ChoiceLoader, FileSystemLoader
from secrets_manager import SecretsConfig, SecretsManager
from starlette.middleware.sessions import SessionMiddleware

from arena.config import settings
from arena.database import create_engine, create_session_factory
from arena.dependencies.access_control import enforce_arena_authentication
from arena.error_handlers import register_error_handlers
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.routes.admin_affiliations import router as arena_admin_affiliations_router
from arena.routes.admin_categories import router as arena_admin_categories_router
from arena.routes.admin_dashboard import router as arena_admin_dashboard_router
from arena.routes.admin_dashboard_history import router as arena_admin_dashboard_history_router
from arena.routes.admin_problem_api import router as arena_admin_problem_api_router
from arena.routes.admin_problem_io import router as arena_admin_problem_io_router
from arena.routes.admin_problem_tc import router as arena_admin_problem_tc_router
from arena.routes.admin_problems import router as arena_admin_problems_router
from arena.routes.admin_users import router as arena_admin_users_router
from arena.routes.admin_users_actions import router as arena_admin_users_actions_router
from arena.routes.affiliations import router as arena_affiliations_router
from arena.routes.auth import router as arena_auth_router
from arena.routes.auth_2fa import router as arena_auth_2fa_router
from arena.routes.auth_password import router as arena_auth_password_router
from arena.routes.auth_signup import router as arena_auth_signup_router
from arena.routes.classes import router as arena_classes_router
from arena.routes.classes_members import router as arena_classes_members_router
from arena.routes.health import router as arena_health_router
from arena.routes.help import router as arena_help_router
from arena.routes.legal import router as arena_legal_router
from arena.routes.live import router as arena_live_router
from arena.routes.notifications import router as arena_notifications_router
from arena.routes.presence import ARENA_PRESENCE_DOMAIN
from arena.routes.presence import router as arena_presence_router
from arena.routes.problem_sets import router as arena_problem_sets_router
from arena.routes.problem_sets_autocomplete import router as arena_problem_sets_autocomplete_router
from arena.routes.problem_sets_batch_feedback import router as arena_problem_sets_batch_feedback_router
from arena.routes.problem_sets_report import router as arena_problem_sets_report_router
from arena.routes.problems import router as arena_problems_router
from arena.routes.ranking import router as arena_ranking_router
from arena.routes.root import router as arena_root_router
from arena.routes.status import router as arena_status_router
from arena.routes.student_problem_sets import router as arena_student_problem_sets_router
from arena.routes.submissions import router as arena_submissions_router
from arena.routes.user_public_profile import router as arena_user_public_profile_router
from arena.routes.user_security import router as arena_user_security_router
from arena.routes.user_submission_status import router as arena_user_submission_status_router
from arena.routes.users import router as arena_users_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.qrcode_service import QRCodeService
from arena.services.session_service import ARENA_REMEMBER_ME_MAX_AGE, get_session_started_at, is_remembered_login
from arena.services.startup_seeds import ensure_sem_afiliacao
from arena.services.token_service import ARENA_JWT_ISSUER, ArenaTokenAction, JWTService, load_token_config_from_dict
from arena.services.user_timezone_service import (
    datetime_local_value,
    format_relative_datetime,
    format_user_datetime,
    timezone_name_for_user,
)
from arena.services.valkey_service import create_arena_valkey_runtime
from shared.app_logging import configure_logging, log_settings
from shared.db_schema.custom_types import init_encrypted_string
from shared.enumerations import VERDICT_BADGE_CLASSES, VERDICT_LABELS, Environment
from shared.services.arena_rating import (
    NEXT_RATING_UPDATE_KEY,
    RATING_AFFILIATION_FACTOR_KEY,
    RATING_INTERVAL_TEXT_KEY,
    format_next_rating_update,
)
from shared.services.email_service import EmailConfig, EmailService
from shared.services.geolocation import GeolocationIP
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService
from shared.services.network_utils import NetworkService
from shared.services.security_events_reaper import run_security_events_reaper
from shared.services.security_headers import SecurityHeaderSettings, SecurityHeadersMiddleware
from shared.services.startup_wait import wait_for_db, wait_for_valkey
from shared.services.token_revocation import ValkeyRevocationStore
from shared.services.user_presence import count_online_users
from shared.static_files import ShortCacheStaticFiles
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES
from shared.timing import format_compact_duration

try:
    APP_VERSION = version("noca-arena")
except PackageNotFoundError:
    APP_VERSION = "dev"

logger = logging.getLogger(__name__)

_ARENA_DIR = Path(__file__).parent
_SHARED_DIR = _ARENA_DIR.parent / "shared"


def validate_crypto_environment() -> None:
    """Validate SecretsManager environment before starting the ASGI server."""
    crypto_env_file = Path(settings.ARENA_CRYPTO_ENV_FILE)

    if not crypto_env_file.exists():
        logger.error("- Crypto environment file not found: %s", crypto_env_file)
        logger.error("  Create the '.env.crypto' file before starting Arena.")
        sys.exit(1)

    load_dotenv(crypto_env_file, override=False)

    try:
        SecretsConfig.from_environment()
    except ValueError:
        logger.error("- SecretsManager environment variables not found or invalid.")
        logger.error("  Create or fix the '%s' file before starting Arena.", crypto_env_file)
        sys.exit(1)


def _next_rating_update_text(request: Request) -> str | None:
    """Return the footer-ready next rating update text for a request.

    Args:
        request: Current FastAPI request whose app state stores scheduler data.

    Returns:
        Relative duration text, or ``None`` when the scheduler has no active deadline.
    """
    return format_next_rating_update(getattr(request.app.state, "next_rating_update", None))


def _arena_online_user_count(request: Request) -> int | None:
    """Return the cached count of online Arena users for footer rendering.

    Reads the value refreshed by ``_online_users_count_poller`` so the
    synchronous template helper needs no per-request Valkey access. Returns
    ``None`` when presence is disabled or the poller has not produced a value.

    Args:
        request: Current FastAPI request whose app state stores the count.

    Returns:
        The online-user count, or ``None`` when unavailable.
    """
    return getattr(request.app.state, "arena_online_user_count", None)


def _token_expiry_text(request: Request) -> str | None:
    """Return a human-readable description of how long the current session is still valid.

    For regular (non-remembered) sessions, reads the current JWT's ``expires_in``
    (time until the token itself expires).

    For remembered sessions, shows the time remaining until the **absolute 30-day cap**
    (``session_started_at + ARENA_REMEMBER_ME_MAX_AGE``) instead of the 1-hour token
    expiry, because the token is silently rotated by middleware and the user should see
    their actual session lifetime, not the short-lived token's TTL.

    Args:
        request: Current FastAPI request whose state carries the validated token.

    Returns:
        A human-readable duration string (e.g. ``"29d 23h"``, ``"2h 30m"``, ``"< 1 min"``),
        or ``None`` when no authenticated session is active.
    """
    validation = getattr(request.state, "validated_token", None)
    if validation is None:
        return None

    remaining: int | None
    if is_remembered_login(validation):
        session_started_at = get_session_started_at(validation)
        if session_started_at is not None:
            abs_deadline = session_started_at + ARENA_REMEMBER_ME_MAX_AGE
            remaining = abs_deadline - int(datetime.now(UTC).timestamp())
        else:
            remaining = ARENA_REMEMBER_ME_MAX_AGE
    else:
        remaining = getattr(validation, "expires_in", None)

    if remaining is None or remaining <= 0:
        return None

    days, rem = divmod(int(remaining), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes > 0:
        return f"{minutes}m"
    return "< 1 min"


async def _next_rating_update_poller(app: FastAPI, stop_event: asyncio.Event) -> None:
    """Mirror the rating worker's next-update timestamp into app state.

    The standalone rating worker publishes the next scheduled cycle timestamp
    and active interval to Valkey. Each Arena instance polls those keys into app
    state so synchronous template helpers can read them without per-request
    Valkey access. Multiple Arena instances polling concurrently is safe — the
    read is idempotent.

    Args:
        app: The Arena FastAPI application.
        stop_event: Setting this event terminates the poller.
    """
    poll_interval_s = 30
    while not stop_event.is_set():
        next_update: datetime | None = None
        rating_interval_text: str | None = None
        affiliation_rating_factor: str | None = None
        try:
            values = await app.state.valkey_runtime.mget(
                [
                    NEXT_RATING_UPDATE_KEY,
                    RATING_INTERVAL_TEXT_KEY,
                    RATING_AFFILIATION_FACTOR_KEY,
                ],
            )
            if values:
                raw_update, raw_interval_text, raw_factor = values
                if raw_update:
                    next_update = datetime.fromisoformat(raw_update)
                rating_interval_text = raw_interval_text or None
                affiliation_rating_factor = raw_factor or None
        except Exception:
            logger.debug("Could not read rating scheduler metadata from Valkey", exc_info=True)
        app.state.next_rating_update = next_update
        app.state.rating_interval_text = rating_interval_text
        app.state.affiliation_rating_factor = affiliation_rating_factor
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)


async def _online_users_count_poller(app: FastAPI, stop_event: asyncio.Event) -> None:
    """Refresh the cached online-user count into app state.

    Counting online users requires aggregating the presence sorted set, which is
    too costly to do on every footer render. Each Arena instance polls the count
    on the heartbeat cadence so the synchronous footer helper can read a cached
    value. Concurrent polling across instances is safe — the read is idempotent.

    Args:
        app: The Arena FastAPI application.
        stop_event: Setting this event terminates the poller.
    """
    poll_interval_s = settings.PRESENCE_HEARTBEAT_SECONDS
    while not stop_event.is_set():
        count = await count_online_users(
            app.state.valkey_runtime,
            domain=ARENA_PRESENCE_DOMAIN,
            ttl_seconds=settings.PRESENCE_TTL_SECONDS,
        )
        # Retain the last valid count on unavailability so a Valkey outage never
        # shows a misleading "0 online"; the footer hides until the first value.
        if count is not None:
            app.state.arena_online_user_count = count
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Manage the Arena application lifespan: start and stop all core services."""
    configure_logging(logging_level=settings.resolved_log_level)
    logger.info("*" * 80)

    logger.info(r" _   _                    ___                       ".center(80, " "))
    logger.info(r"| \ | |                  / _ \                      ".center(80, " "))
    logger.info(r"|  \| | ___   ___ __ _  / /_\ \_ __ ___ _ __   __ _ ".center(80, " "))
    logger.info(r"| . ` |/ _ \ / __/ _` | |  _  | '__/ _ \ '_ \ / _` |".center(80, " "))
    logger.info(r"| |\  | (_) | (_| (_| | | | | | | |  __/ | | | (_| |".center(80, " "))
    logger.info(r"\_| \_/\___/ \___\__,_| \_| |_/_|  \___|_| |_|\__,_|".center(80, " "))
    logger.info(" " * 80)

    banner = f"Starting Arena (version {APP_VERSION}, environment {settings.ENVIRONMENT.value})"
    logger.info(banner.center(80, " "))
    logger.info("-" * 80)
    logger.info("Problem test case directory: %s", settings.PROBLEM_TESTCASE_DIR)
    logger.info("| Initializing services |".center(80, "-"))
    log_settings(logger, settings)

    await wait_for_db(settings.db_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)
    await wait_for_valkey(settings.valkey_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)

    validate_crypto_environment()
    secrets_config = SecretsConfig.from_environment()
    secrets_manager = SecretsManager(secrets_config)
    init_encrypted_string(secrets_manager)
    app.state.secrets_manager = secrets_manager
    logger.info("- SecretsManager initialized (active_version=%s)", secrets_manager.get_active_version())

    engine = create_engine(settings.db_url)
    app.state.arena_db_engine = engine
    app.state.arena_db_session = create_session_factory(engine)
    logger.info("- Database connection pool opened")

    await ensure_sem_afiliacao(app.state.arena_db_session)
    logger.info('- Startup seed: "%s" affiliation ensured (exclude_from_ranking=True)', "Sem afiliação")

    valkey_runtime = create_arena_valkey_runtime(
        healthcheck_interval_s=settings.VALKEY_HEALTHCHECK_INTERVAL_SECONDS,
    )
    await valkey_runtime.start()
    app.state.valkey_runtime = valkey_runtime
    logger.info("- Valkey runtime started")

    revocation_store = ValkeyRevocationStore(valkey_url=settings.valkey_url, logger=logger)
    app.state.revocation_store = revocation_store
    logger.info("- ValkeyRevocationStore started")

    jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": settings.JWT_SECRET_KEY,
                "JWTSERVICE_ALGORITHM": settings.JWT_ALGORITHM,
                "JWTSERVICE_ISSUER": ARENA_JWT_ISSUER,
            }
        ),
        logger=logger,
        action_enum=ArenaTokenAction,
        revocation_store=revocation_store,
    )
    app.state.jwt_service = jwt_service
    logger.info("- JWTService started")

    email_config = EmailConfig.from_settings(settings)
    app.state.email_service = EmailService(config=email_config, logger=logger)
    logger.info(
        "- EmailService started (provider=%s, send_email=%s)",
        email_config.provider_type,
        email_config.send_email,
    )
    logger.info(
        "- Email mbox logging configured (enabled=%s, directory=%s)",
        email_config.send_email and email_config.provider_type.casefold() == "smtp" and bool(email_config.mbox_log_dir),
        email_config.mbox_log_dir or "N/A",
    )

    image_config = ImageProcessingConfig(
        avatar_size=settings.IMAGE_AVATAR_SIZE,
        max_file_size=settings.IMAGE_MAX_FILE_SIZE,
        max_width=settings.IMAGE_MAX_WIDTH,
        max_height=settings.IMAGE_MAX_HEIGHT,
        response_cache_max_age=settings.IMAGE_RESPONSE_CACHE_MAX_AGE,
        font_dir=settings.IMAGE_FONT_DIR,
    )
    app.state.image_service = ImageProcessingService(config=image_config, logger=logger)
    logger.info("- ImageProcessingService started")

    app.state.qrcode_service = QRCodeService.create_default()
    logger.info("- QRCodeService started")

    geo_service = GeolocationIP(
        api_key=settings.GEOLOCATION_API_KEY,
        network_service=NetworkService(logger=logger),
        logger=logger,
    )
    app.state.geo_service = geo_service
    logger.info(
        "- GeolocationIP service initialised (enabled=%s)",
        settings.GEOLOCATION_API_KEY is not None,
    )
    app.state.reverse_geocoder_network_service = NetworkService(logger=logger)
    app.state.reverse_geocoder_user_agent = (
        settings.ARENA_REVERSE_GEOCODER_USER_AGENT or f"{settings.APP_NAME}/{APP_VERSION}"
    )
    logger.info(
        "- Arena profile reverse geocoder initialised (enabled=%s)",
        settings.ARENA_REVERSE_GEOCODER_ENABLED,
    )

    def _fmt_shell_cmd(cmd: list[str] | None) -> str:
        """Join a command list and split on && for readable display."""
        if not cmd:
            return ""
        return " && \\\n".join(" ".join(cmd).split(" && "))

    arena_templates = Jinja2Templates(directory=_ARENA_DIR / "template")
    arena_templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(_ARENA_DIR / "template")),
            FileSystemLoader(str(_SHARED_DIR / "template")),
        ]
    )
    arena_templates.env.globals["MAX_INLINE_TESTCASE_BYTES"] = MAX_INLINE_TESTCASE_BYTES
    arena_templates.env.filters["fmt_shell_cmd"] = _fmt_shell_cmd
    arena_templates.env.globals["arena_datetime_local_value"] = datetime_local_value
    arena_templates.env.globals["arena_format_datetime"] = format_user_datetime
    arena_templates.env.globals["arena_format_relative_datetime"] = format_relative_datetime
    arena_templates.env.globals["format_compact_duration"] = format_compact_duration
    arena_templates.env.globals["arena_user_timezone_name"] = timezone_name_for_user
    arena_templates.env.globals["app_version"] = APP_VERSION
    arena_templates.env.globals["presence_enabled"] = settings.PRESENCE_ENABLED
    arena_templates.env.globals["presence_heartbeat_seconds"] = settings.PRESENCE_HEARTBEAT_SECONDS
    arena_templates.env.globals["arena_online_user_count"] = _arena_online_user_count
    arena_templates.env.globals["next_rating_update_text"] = _next_rating_update_text
    arena_templates.env.globals["token_expiry_text"] = _token_expiry_text
    arena_templates.env.globals["verdict_badge_classes"] = VERDICT_BADGE_CLASSES
    arena_templates.env.globals["verdict_labels"] = VERDICT_LABELS
    setup_flash(arena_templates)
    arena_templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    app.state.arena_templates = arena_templates
    logger.info("- Jinja2 templates initialised (directory=%s)", _ARENA_DIR / "template")

    rating_poller_stop = asyncio.Event()
    app.state.rating_poller_stop = rating_poller_stop
    app.state.next_rating_update = None
    app.state.rating_interval_text = None
    app.state.affiliation_rating_factor = None
    app.state.rating_poller_task = asyncio.create_task(
        _next_rating_update_poller(app, rating_poller_stop),
        name="rating-update-poller",
    )
    logger.info(
        "- Rating scheduler metadata poller started (source keys=%s,%s,%s)",
        NEXT_RATING_UPDATE_KEY,
        RATING_INTERVAL_TEXT_KEY,
        RATING_AFFILIATION_FACTOR_KEY,
    )

    app.state.online_count_poller_task = None
    app.state.arena_online_user_count = None
    if settings.PRESENCE_ENABLED:
        online_count_poller_stop = asyncio.Event()
        app.state.online_count_poller_stop = online_count_poller_stop
        app.state.online_count_poller_task = asyncio.create_task(
            _online_users_count_poller(app, online_count_poller_stop),
            name="online-users-count-poller",
        )
        logger.info("- Online-users count poller started")

    app.state.security_events_reaper_stop = asyncio.Event()
    app.state.security_events_reaper_task = None
    if settings.SECURITY_EVENTS_RETENTION_DAYS > 0:
        app.state.security_events_reaper_task = asyncio.create_task(
            run_security_events_reaper(
                app.state.arena_db_session,
                poll_interval_seconds=settings.SECURITY_EVENTS_REAPER_INTERVAL_SECONDS,
                retention_days=settings.SECURITY_EVENTS_RETENTION_DAYS,
                modules=["arena", "aiassistant"],
                stop_event=app.state.security_events_reaper_stop,
                logger=logger,
            ),
            name="security-events-reaper",
        )
        logger.info(
            "- Security-events reaper started (interval=%ss, retention=%sd, modules=arena,aiassistant)",
            settings.SECURITY_EVENTS_REAPER_INTERVAL_SECONDS,
            settings.SECURITY_EVENTS_RETENTION_DAYS,
        )
    else:
        logger.warning("- Security-events reaper disabled (retention=0)")

    logger.info("| Arena running |".center(80, "-"))

    yield

    logger.info("*" * 80)

    app.state.rating_poller_stop.set()
    await asyncio.gather(app.state.rating_poller_task, return_exceptions=True)
    logger.info("Next-rating-update poller stopped")

    if app.state.online_count_poller_task is not None:
        app.state.online_count_poller_stop.set()
        await asyncio.gather(app.state.online_count_poller_task, return_exceptions=True)
        logger.info("Online-users count poller stopped")

    if app.state.security_events_reaper_task is not None:
        app.state.security_events_reaper_stop.set()
        await asyncio.gather(app.state.security_events_reaper_task, return_exceptions=True)
        logger.info("Security-events reaper stopped")

    app.state.revocation_store.close()
    logger.info("ValkeyRevocationStore closed")

    await app.state.valkey_runtime.stop()
    logger.info("Valkey runtime stopped")

    await engine.dispose()
    logger.info("Database connection pool closed")

    logger.info("*" * 80)
    logger.info("Arena stopped")
    logger.info("*" * 80)


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    dependencies=[Depends(enforce_arena_authentication)],
)
register_error_handlers(app)

# Middleware order matters: SessionMiddleware must wrap ArenaAuthMiddleware so
# that the session is available when the auth middleware writes flash messages
# via the dependency.  FastAPI processes middleware in reverse registration
# order (last registered = outermost), so SessionMiddleware is registered last.
app.add_middleware(
    SecurityHeadersMiddleware,
    settings=SecurityHeaderSettings(
        enabled=settings.SECURITY_HEADERS_ENABLED,
        csp_report_only=settings.CSP_REPORT_ONLY,
        hsts_enabled=settings.COOKIE_SECURE or settings.ENVIRONMENT == Environment.PRODUCTION,
    ),
)
app.add_middleware(ArenaAuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.JWT_SECRET_KEY,
    https_only=settings.COOKIE_SECURE,
)


app.mount(
    "/static/css",
    ShortCacheStaticFiles(directory=_ARENA_DIR / "static" / "css"),
    name="arena_static_css",
)

app.mount(
    "/static/shared-css",
    ShortCacheStaticFiles(directory=_SHARED_DIR / "static" / "css"),
    name="static_shared_css",
)

app.mount(
    "/static/js",
    ShortCacheStaticFiles(directory=_ARENA_DIR / "static" / "js"),
    name="arena_static_js",
)

app.mount(
    "/static/shared-js",
    ShortCacheStaticFiles(directory=_SHARED_DIR / "static" / "js"),
    name="static_shared_js",
)

app.mount(
    "/static/img",
    StaticFiles(directory=_ARENA_DIR / "static" / "img"),
    name="arena_static_img",
)

app.mount(
    "/static/vendor",
    StaticFiles(directory=_SHARED_DIR / "static" / "vendor"),
    name="static_vendor",
)

app.mount(
    "/static/webfonts",
    StaticFiles(directory=_SHARED_DIR / "static" / "webfonts"),
    name="static_webfonts",
)

# ###################################################################
# Main routes
app.include_router(arena_root_router)
app.include_router(arena_health_router)
app.include_router(arena_status_router)
app.include_router(arena_live_router)
app.include_router(arena_auth_router)
app.include_router(arena_auth_signup_router)
app.include_router(arena_auth_2fa_router)
app.include_router(arena_auth_password_router)
app.include_router(arena_classes_router)
app.include_router(arena_classes_members_router)
app.include_router(arena_problem_sets_router)
app.include_router(arena_problem_sets_report_router)
app.include_router(arena_problem_sets_batch_feedback_router)
app.include_router(arena_problem_sets_autocomplete_router)
app.include_router(arena_student_problem_sets_router)
app.include_router(arena_submissions_router)
app.include_router(arena_legal_router)
app.include_router(arena_help_router)
app.include_router(arena_problems_router)
app.include_router(arena_users_router)
app.include_router(arena_user_public_profile_router)
app.include_router(arena_user_submission_status_router)
app.include_router(arena_user_security_router)
app.include_router(arena_notifications_router)
app.include_router(arena_presence_router)
app.include_router(arena_affiliations_router)
app.include_router(arena_ranking_router)
app.include_router(arena_admin_affiliations_router)
app.include_router(arena_admin_categories_router)
app.include_router(arena_admin_dashboard_router)
app.include_router(arena_admin_dashboard_history_router)
app.include_router(arena_admin_users_router)
app.include_router(arena_admin_users_actions_router)
app.include_router(arena_admin_problems_router)
app.include_router(arena_admin_problem_io_router)
app.include_router(arena_admin_problem_tc_router)
app.include_router(arena_admin_problem_api_router)


def main() -> None:
    """Entry point for ``uv run noca-arena``."""
    configure_logging(logging_level=settings.resolved_log_level)
    validate_crypto_environment()
    reload_enabled = settings.ENVIRONMENT == Environment.DEVELOPMENT
    uvicorn.run(
        "arena.main:app",
        host="0.0.0.0",
        port=8001,
        reload=reload_enabled,
        reload_dirs=["arena", "shared"] if reload_enabled else None,
        proxy_headers=True,
        forwarded_allow_ips=settings.FORWARDED_ALLOW_IPS,
        log_config=None,
        log_level="warning",
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
