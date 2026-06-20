#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from jinja2 import ChoiceLoader, FileSystemLoader
from jwtservice import JWTService, load_token_config_from_dict
from starlette.middleware.sessions import SessionMiddleware

from shared.app_logging import configure_logging, log_settings
from shared.enumerations import (
    VERDICT_BADGE_CLASSES,
    VERDICT_LABELS,
    Environment,
    JudgmentStatus,
    RoleEnum,
    TaskType,
    Verdict,
)
from shared.services.email_service import EmailConfig, EmailService
from shared.services.geolocation import GeolocationIP
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService
from shared.services.network_utils import NetworkService
from shared.services.startup_wait import wait_for_db, wait_for_valkey
from shared.services.token_revocation import ValkeyRevocationStore
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES
from web.config import settings
from web.database import create_engine, create_session_factory
from web.error_handlers import register_error_handlers
from web.middleware.auth_token_refresh import AuthTokenRefreshMiddleware
from web.routes.assets import router as assets_router
from web.routes.auth import router as login_logout_router
from web.routes.contest_admin import router as contest_admin_router
from web.routes.contest_admin_export import router as contest_admin_export_router
from web.routes.contest_admin_metadata import router as contest_admin_metadata_router
from web.routes.contest_admin_problem import router as contest_admin_problem_router
from web.routes.contest_admin_problem_categories import router as contest_admin_problem_categories_router
from web.routes.contest_admin_problem_edit import router as contest_admin_problem_edit_router
from web.routes.contest_admin_problem_io import router as contest_admin_problem_io_router
from web.routes.contest_admin_problem_limits import router as contest_admin_problem_limits_router
from web.routes.contest_admin_problem_tc import router as contest_admin_problem_tc_router
from web.routes.contest_admin_reports import router as contest_admin_reports_router
from web.routes.contest_admin_user import router as contest_admin_user_router
from web.routes.contest_admin_user_batch import router as contest_admin_user_batch_router
from web.routes.contest_admin_user_edit import router as contest_admin_user_edit_router
from web.routes.contest_clarifications import router as contest_clarifications_router
from web.routes.contest_clarifications_admin import router as contest_clarifications_admin_router
from web.routes.contest_clarifications_judge import router as contest_clarifications_judge_router
from web.routes.contest_clarifications_submit import router as contest_clarifications_submit_router
from web.routes.contest_live_feed import router as contest_live_feed_router
from web.routes.contest_problems import router as contest_problems_router
from web.routes.contest_reports import router as contest_reports_router
from web.routes.contest_runs import router as contest_runs_router
from web.routes.contest_runs_events import router as contest_runs_events_router
from web.routes.contest_runs_review import router as contest_runs_review_router
from web.routes.contest_score import router as contest_score_router
from web.routes.contest_submissions import router as contest_submissions_router
from web.routes.contest_submissions_files import router as contest_submissions_files_router
from web.routes.contest_submissions_review import router as contest_submissions_review_router
from web.routes.contest_tasks import router as contest_tasks_router
from web.routes.contest_tasks_staff import router as contest_tasks_staff_router
from web.routes.generaluser_dashboard import router as generaluser_dashboard_router
from web.routes.health import router as health_router
from web.routes.profile import router as profile_router
from web.routes.root import router as root_router
from web.routes.uberadmin_dashboard import router as uberadmin_dashboard_router
from web.routes.uberadmin_users import router as uberadmin_users_router
from web.services.assorted_utils import contest_minutes, contest_verdict_badge_class, format_site_identity
from web.services.authentication_service import AuthAction, AuthenticationService
from web.services.clarification_reaper import run_clarification_reaper
from web.services.task_reaper import run_task_reaper
from web.services.valkey_service import ValkeyRuntime

try:
    APP_VERSION = version("noca-web")
except PackageNotFoundError:
    APP_VERSION = "dev"

logger = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent
_SHARED_DIR = _WEB_DIR.parent / "shared"

_network_service = NetworkService(logger=logger)
_geolocation_service = GeolocationIP(
    api_key=settings.GEOLOCATION_API_KEY, network_service=_network_service, logger=logger
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    from web.config import settings as config

    configure_logging(logging_level=settings.resolved_log_level)
    logger.info("*" * 80)

    logger.info(r" _   _                   _____             _            _   ".center(80, " "))
    logger.info(r"| \ | |                 /  __ \           | |          | |  ".center(80, " "))
    logger.info(r"|  \| | ___   ___ __ _  | /  \/ ___  _ __ | |_ ___  ___| |_ ".center(80, " "))
    logger.info(r"| . ` |/ _ \ / __/ _` | | |    / _ \| '_ \| __/ _ \/ __| __|".center(80, " "))
    logger.info(r"| |\  | (_) | (_| (_| | | \__/\ (_) | | | | ||  __/\__ \ |_ ".center(80, " "))
    logger.info(r"\_| \_/\___/ \___\__,_|  \____/\___/|_| |_|\__\___||___/\__|".center(80, " "))
    logger.info(" " * 80)

    banner = f"Starting Contest (version {APP_VERSION}, environment {settings.ENVIRONMENT.value})"
    logger.info(banner.center(80, " "))
    logger.info("-" * 80)
    logger.info("Problem statements directory: %s", config.PROBLEM_STATEMENT_DIR)
    logger.info("Problem test case directory: %s", config.PROBLEM_TESTCASE_DIR)
    logger.info("| Initializing services |".center(80, "-"))
    log_settings(logger, settings)
    await wait_for_db(settings.db_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)
    await wait_for_valkey(settings.valkey_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)
    engine = create_engine()
    app.state.db_engine = engine
    app.state.db_session = create_session_factory(engine)
    logger.info("- Database connection pool opened")

    valkey_runtime = ValkeyRuntime(
        valkey_url=settings.valkey_url,
        healthcheck_interval_s=settings.VALKEY_HEALTHCHECK_INTERVAL_SECONDS,
    )
    await valkey_runtime.start()
    app.state.valkey_runtime = valkey_runtime
    logger.info("- Valkey runtime started")
    app.state.clarification_reaper_stop = asyncio.Event()
    app.state.clarification_reaper_task = None
    app.state.task_reaper_stop = asyncio.Event()
    app.state.task_reaper_task = None

    revocation_store = ValkeyRevocationStore(valkey_url=settings.valkey_url, logger=logger)
    app.state.revocation_store = revocation_store
    logger.info("- ValkeyRevocationStore started")

    jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": settings.JWT_SECRET_KEY,
                "JWTSERVICE_ALGORITHM": settings.JWT_ALGORITHM,
                "JWTSERVICE_ISSUER": settings.APP_NAME,
            }
        ),
        logger=logger,
        action_enum=AuthAction,
        revocation_store=revocation_store,
    )
    logger.info("- JWTService started")

    app.state.auth_service = AuthenticationService(
        jwt_service=jwt_service, geolocation_service=_geolocation_service, logger=logger
    )
    logger.info("- AuthenticationService started")

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

    templates = Jinja2Templates(directory=_WEB_DIR / "template")
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(_WEB_DIR / "template")),
            FileSystemLoader(str(_SHARED_DIR / "template")),
        ]
    )
    templates.env.globals["app_version"] = APP_VERSION
    templates.env.globals["MAX_INLINE_TESTCASE_BYTES"] = MAX_INLINE_TESTCASE_BYTES
    templates.env.globals["contest_minutes"] = contest_minutes
    templates.env.globals["format_site_identity"] = format_site_identity
    templates.env.globals["RoleEnum"] = RoleEnum
    templates.env.globals["Verdict"] = Verdict
    templates.env.globals["JudgmentStatus"] = JudgmentStatus
    templates.env.globals["role_labels"] = {
        RoleEnum.UBERADMIN.value: "Uber Admin",
        RoleEnum.ADMIN.value: "Admin",
        RoleEnum.JUDGE.value: "Judge",
        RoleEnum.STAFF.value: "Staff",
        RoleEnum.TEAM.value: "Team",
        RoleEnum.USER.value: "User",
    }
    templates.env.globals["verdict_labels"] = VERDICT_LABELS
    templates.env.globals["verdict_badge_classes"] = VERDICT_BADGE_CLASSES
    templates.env.globals["contest_verdict_badge_class"] = contest_verdict_badge_class
    templates.env.globals["judgmentstatus_labels"] = {
        JudgmentStatus.QUEUED.value: "Queue",
        JudgmentStatus.DISPATCHED.value: "Dispatched",
        JudgmentStatus.JUDGING.value: "Judging",
        JudgmentStatus.DONE.value: "Done",
        JudgmentStatus.FAILED.value: "Failed. Will retry",
        JudgmentStatus.SUPERSEDED.value: "Superseded by other judgment",
    }
    templates.env.globals["TaskType"] = TaskType
    templates.env.globals["judgmentstatus_badge_classes"] = {
        JudgmentStatus.QUEUED.value: "bg-secondary",
        JudgmentStatus.DISPATCHED.value: "bg-info text-dark",
        JudgmentStatus.JUDGING.value: "bg-primary",
        JudgmentStatus.DONE.value: "bg-success",
        JudgmentStatus.FAILED.value: "bg-danger",
        JudgmentStatus.SUPERSEDED.value: "bg-light text-muted",
    }

    def _utc_to_local(utc_iso: str, iana_tz: str) -> str:
        """Convert a UTC ISO 8601 string to local time formatted as 'YYYY-MM-DD HH:MM:SS (iana_tz)'."""
        from datetime import UTC, datetime
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            dt = datetime.fromisoformat(utc_iso.rstrip("Z")).replace(tzinfo=UTC)
            local_dt = dt.astimezone(ZoneInfo(iana_tz))
            return local_dt.strftime("%Y-%m-%d %H:%M:%S") + f" ({iana_tz})"
        except ValueError:
            return utc_iso
        except ZoneInfoNotFoundError:
            return utc_iso

    templates.env.filters["utc_to_local"] = _utc_to_local
    setup_flash(templates)  # registers get_flashed_messages in the Jinja2 environment
    app.state.templates = templates

    if settings.ENABLE_CLARIFICATION_REAPER:
        poll_interval_seconds = settings.CLARIFICATION_REAPER_INTERVAL_SECONDS
        app.state.clarification_reaper_task = asyncio.create_task(
            run_clarification_reaper(
                app.state.db_session,
                poll_interval_seconds=poll_interval_seconds,
                stop_event=app.state.clarification_reaper_stop,
                logger=logger,
            ),
            name="clarification-reaper",
        )
        logger.info("- Clarification reaper enabled (interval=%ss)", poll_interval_seconds)
    else:
        logger.warning("- Clarification reaper disabled")

    if settings.ENABLE_TASK_REAPER:
        poll_interval_seconds = settings.TASK_REAPER_INTERVAL_SECONDS
        app.state.task_reaper_task = asyncio.create_task(
            run_task_reaper(
                app.state.db_session,
                poll_interval_seconds=poll_interval_seconds,
                stop_event=app.state.task_reaper_stop,
                logger=logger,
            ),
            name="task-reaper",
        )
        logger.info("- Task reaper enabled (interval=%ss)", poll_interval_seconds)
    else:
        logger.warning("- Task reaper disabled")

    # logger.debug("| Registered routes |".center(80, "-"))
    # for route in app.routes:
    #     if hasattr(route, "methods") and hasattr(route, "path"):
    #         methods = ", ".join(sorted(getattr(route, "methods", set()) or set()))
    #         path = str(getattr(route, "path", ""))
    #         name = str(getattr(route, "name", "") or "")
    #         logger.debug("  %-7s %-60s %s", methods, path, name)

    logger.info("| Contest running |".center(80, "-"))

    yield

    logger.info("*" * 80)
    reaper_task = app.state.clarification_reaper_task
    if reaper_task is not None:
        app.state.clarification_reaper_stop.set()
        await reaper_task
        logger.info("Clarification reaper stopped")

    task_reaper_task = app.state.task_reaper_task
    if task_reaper_task is not None:
        app.state.task_reaper_stop.set()
        await task_reaper_task
        logger.info("Task reaper stopped")

    app.state.revocation_store.close()
    logger.info("ValkeyRevocationStore closed")

    await app.state.valkey_runtime.stop()
    logger.info("Valkey runtime stopped")

    await engine.dispose()
    logger.info("Database connection pool closed")
    logger.info("*" * 80)
    logger.info("Contest stopped")
    logger.info("*" * 80)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
register_error_handlers(app)

app.add_middleware(SessionMiddleware, secret_key=settings.JWT_SECRET_KEY)
app.add_middleware(AuthTokenRefreshMiddleware)

app.mount("/static/js", StaticFiles(directory=_WEB_DIR / "static" / "js"), name="static_js")
app.mount("/static/shared-js", StaticFiles(directory=_SHARED_DIR / "static" / "js"), name="static_shared_js")
app.mount("/static/css", StaticFiles(directory=_WEB_DIR / "static" / "css"), name="static_css")
app.mount("/static/shared-css", StaticFiles(directory=_SHARED_DIR / "static" / "css"), name="static_shared_css")
app.mount("/static/img", StaticFiles(directory=_WEB_DIR / "static" / "img"), name="static_img")
app.mount("/static/vendor", StaticFiles(directory=_SHARED_DIR / "static" / "vendor"), name="static_vendor")
app.mount("/static/webfonts", StaticFiles(directory=_SHARED_DIR / "static" / "webfonts"), name="static_webfonts")

# ###################################################################
# Main routes
app.include_router(assets_router)
app.include_router(login_logout_router)
app.include_router(root_router)
app.include_router(health_router)

# ###################################################################
# Dashboard routes
app.include_router(uberadmin_dashboard_router)
app.include_router(uberadmin_users_router)
app.include_router(generaluser_dashboard_router)

# ###################################################################
# Contest routes
app.include_router(contest_score_router)
app.include_router(contest_problems_router)
app.include_router(contest_clarifications_router)
app.include_router(contest_clarifications_submit_router)
app.include_router(contest_clarifications_judge_router)
app.include_router(contest_clarifications_admin_router)
app.include_router(contest_runs_router)
app.include_router(contest_runs_review_router)
app.include_router(contest_runs_events_router)
app.include_router(contest_live_feed_router)
app.include_router(contest_submissions_router)
app.include_router(contest_submissions_review_router)
app.include_router(contest_submissions_files_router)
app.include_router(contest_tasks_router)
app.include_router(contest_tasks_staff_router)
app.include_router(contest_reports_router)
app.include_router(contest_admin_problem_router)
app.include_router(contest_admin_problem_edit_router)
app.include_router(contest_admin_problem_limits_router)
app.include_router(contest_admin_problem_categories_router)
app.include_router(contest_admin_problem_io_router)
app.include_router(contest_admin_problem_tc_router)
app.include_router(contest_admin_user_router)
app.include_router(contest_admin_user_batch_router)
app.include_router(contest_admin_user_edit_router)
app.include_router(contest_admin_router)
app.include_router(contest_admin_metadata_router)
app.include_router(contest_admin_reports_router)
app.include_router(contest_admin_export_router)
app.include_router(profile_router)


def main() -> None:
    configure_logging(logging_level=settings.resolved_log_level)
    reload_enabled = settings.ENVIRONMENT == Environment.DEVELOPMENT
    uvicorn.run(
        "web.main:app",
        host="0.0.0.0",
        port=8000,
        reload=reload_enabled,
        reload_dirs=["web", "shared"] if reload_enabled else None,
        proxy_headers=True,
        forwarded_allow_ips=settings.FORWARDED_ALLOW_IPS,
        log_config=None,
        log_level="warning",
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
