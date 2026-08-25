#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Animator FastAPI application (default port 8003).

Standalone live scoreboard and reveal presentation runtime. This scaffold boots
PostgreSQL and Valkey connectivity, initialises templates and static mounts, and
serves a readiness health endpoint. Contest data feeds are added in later phases.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from animator.config import settings
from animator.database import create_engine, create_session_factory
from animator.error_handlers import register_error_handlers
from animator.routes.assets import router as assets_router
from animator.routes.control import router as control_router
from animator.routes.control_page import router as control_page_router
from animator.routes.controller_lease import router as controller_lease_router
from animator.routes.health import router as health_router
from animator.routes.index import router as index_router
from animator.routes.public import router as public_router
from animator.routes.reveal_public import router as reveal_public_router
from animator.routes.team_media import router as team_media_router
from animator.services.event_stream_service import AnimatorEventStream
from shared.app_logging import configure_logging
from shared.enumerations import Environment
from shared.services.security_headers import SecurityHeaderSettings, SecurityHeadersMiddleware
from shared.services.startup_wait import wait_for_db, wait_for_valkey
from shared.services.valkey_service import (
    ValkeyRuntime,
    WorkerClass,
    mark_worker_offline,
    prune_all_stale_workers,
    resolve_worker_id,
    worker_presence_loop,
)
from shared.static_files import RevalidatedStaticFiles

try:
    APP_VERSION = version("noca-animator")
except PackageNotFoundError:
    APP_VERSION = "dev"

logger = logging.getLogger(__name__)

_ANIMATOR_DIR = Path(__file__).resolve().parent
_SHARED_DIR = _ANIMATOR_DIR.parent / "shared"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Boot database and Valkey connectivity, templates, and clean shutdown."""
    configure_logging(logging_level=settings.resolved_log_level)
    logger.info("*" * 80)
    logger.info(" " * 80)
    logger.info(r"  ___        _                 _             ".center(80, " "))
    logger.info(r" / _ \      (_)               | |            ".center(80, " "))
    logger.info(r"/ /_\ \_ __  _ _ __ ___   __ _| |_ ___  _ __ ".center(80, " "))
    logger.info(r"|  _  | '_ \| | '_ ` _ \ / _` | __/ _ \| '__|".center(80, " "))
    logger.info(r"| | | | | | | | | | | | | (_| | || (_) | |   ".center(80, " "))
    logger.info(r"\_| |_/_| |_|_|_| |_| |_|\__,_|\__\___/|_|   ".center(80, " "))
    logger.info(" " * 80)
    banner = f"Starting Animator (version {APP_VERSION}, environment {settings.ENVIRONMENT.value})"
    logger.info(banner.center(80, " "))
    logger.info("*" * 80)

    await wait_for_db(settings.db_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)
    await wait_for_valkey(settings.valkey_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)

    engine = create_engine(settings.db_url)
    app.state.db_engine = engine
    app.state.db_session = create_session_factory(engine)
    logger.info("- Database connection pool opened")

    valkey_runtime: ValkeyRuntime | None = None
    event_stream: AnimatorEventStream | None = None
    presence_stop: asyncio.Event | None = None
    presence_task: asyncio.Task[None] | None = None
    worker_id: str | None = None
    try:
        valkey_runtime = ValkeyRuntime(
            valkey_url=settings.valkey_url,
            healthcheck_interval_s=settings.VALKEY_HEALTHCHECK_INTERVAL_SECONDS,
        )
        await valkey_runtime.start()
        app.state.valkey_runtime = valkey_runtime
        logger.info("- Valkey runtime started")

        event_stream = AnimatorEventStream(valkey_runtime)
        await event_stream.start()
        app.state.event_stream = event_stream
        logger.info("- Animator event stream started")

        # Presence-only heartbeat: the animator is watched by the health monitor
        # but never appears in the Arena worker dashboard or its pause machinery.
        presence_stop = asyncio.Event()
        worker_id = resolve_worker_id(settings.WORKER_ID)
        app.state.worker_presence_stop = presence_stop
        app.state.worker_id = worker_id
        presence_task = asyncio.create_task(
            worker_presence_loop(
                valkey_runtime,
                worker_class=WorkerClass.ANIMATOR,
                worker_id=worker_id,
                started_at=datetime.now(UTC),
                interval_seconds=settings.WORKER_PRESENCE_INTERVAL_SECONDS,
                ttl_seconds=settings.WORKER_PRESENCE_TTL_SECONDS,
                stop_event=presence_stop,
            ),
            name="worker-presence-loop",
        )
        app.state.worker_presence_task = presence_task
        logger.info(
            "- Worker-presence heartbeat started (class=%s, worker_id=%s, interval=%ss, ttl=%ss)",
            WorkerClass.ANIMATOR.value,
            worker_id,
            settings.WORKER_PRESENCE_INTERVAL_SECONDS,
            settings.WORKER_PRESENCE_TTL_SECONDS,
        )

        templates = Jinja2Templates(directory=_ANIMATOR_DIR / "template")
        templates.env.loader = ChoiceLoader(
            [
                FileSystemLoader(str(_ANIMATOR_DIR / "template")),
                FileSystemLoader(str(_SHARED_DIR / "template")),
            ]
        )
        templates.env.globals["app_version"] = APP_VERSION
        templates.env.globals["brand_name"] = settings.BRAND_NAME
        templates.env.globals["healthmon_url"] = settings.HEALTHMON_URL
        app.state.templates = templates
        logger.info("- Jinja2 templates initialised (directory=%s)", _ANIMATOR_DIR / "template")

        logger.info("| Animator running |".center(80, "-"))

        yield
    finally:
        logger.info("*" * 80)
        try:
            # Presence is retired first, while Valkey is still up: the live
            # marker can only be cleared through the runtime torn down below.
            # Wrapped in its own try/finally so a failing heartbeat stop can
            # never skip event-stream, Valkey, or database cleanup.
            try:
                if presence_task is not None:
                    if presence_stop is not None:
                        presence_stop.set()
                    # gather(return_exceptions=True): an already-failed heartbeat
                    # task must not stop us from clearing the live marker.
                    await asyncio.gather(presence_task, return_exceptions=True)
                    if valkey_runtime is not None and worker_id is not None:
                        with contextlib.suppress(Exception):
                            await mark_worker_offline(
                                valkey_runtime,
                                worker_class=WorkerClass.ANIMATOR,
                                worker_id=worker_id,
                            )
                            # Bounds the durable presence registry even where the
                            # optional health monitor (which runs the same pass
                            # periodically) is not deployed.
                            await prune_all_stale_workers(valkey_runtime)
                    logger.info("Worker-presence heartbeat stopped")
            finally:
                # Stop the event stream before the Valkey runtime it borrows its
                # pub/sub connection from. Nested try/finally so a failing
                # event-stream shutdown (its subscriber may re-raise an
                # unrecoverable error) never skips Valkey cleanup.
                try:
                    if event_stream is not None:
                        await event_stream.stop()
                        logger.info("Animator event stream stopped")
                finally:
                    if valkey_runtime is not None:
                        await valkey_runtime.stop()
                        logger.info("Valkey runtime stopped")
        finally:
            # Always release the database pool, even if Valkey shutdown raises
            # (e.g. the health task terminated with a non-recoverable exception).
            await engine.dispose()
            logger.info("Database connection pool closed")
        logger.info("*" * 80)
        logger.info("Animator stopped")
        logger.info("*" * 80)


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

register_error_handlers(app)

# The animator has no cookies of its own, so HSTS keys off the environment alone
# rather than a COOKIE_SECURE flag as in web and arena.
app.add_middleware(
    SecurityHeadersMiddleware,
    settings=SecurityHeaderSettings(
        enabled=settings.SECURITY_HEADERS_ENABLED,
        csp_report_only=settings.CSP_REPORT_ONLY,
        hsts_enabled=settings.ENVIRONMENT == Environment.PRODUCTION,
    ),
)

app.mount(
    "/static/css",
    RevalidatedStaticFiles(directory=_ANIMATOR_DIR / "static" / "css"),
    name="animator_static_css",
)
app.mount(
    "/static/js",
    RevalidatedStaticFiles(directory=_ANIMATOR_DIR / "static" / "js"),
    name="animator_static_js",
)
app.mount(
    "/static/img",
    RevalidatedStaticFiles(directory=_ANIMATOR_DIR / "static" / "img"),
    name="animator_static_img",
)
app.mount(
    "/static/shared-css",
    RevalidatedStaticFiles(directory=_SHARED_DIR / "static" / "css"),
    name="static_shared_css",
)
app.mount(
    "/static/shared-js",
    RevalidatedStaticFiles(directory=_SHARED_DIR / "static" / "js"),
    name="static_shared_js",
)
app.mount("/static/vendor", StaticFiles(directory=_SHARED_DIR / "static" / "vendor"), name="static_vendor")
app.mount("/static/webfonts", StaticFiles(directory=_SHARED_DIR / "static" / "webfonts"), name="static_webfonts")

app.include_router(health_router)
app.include_router(assets_router)
app.include_router(index_router)
app.include_router(public_router)
app.include_router(reveal_public_router)
app.include_router(team_media_router)
# The operator's HTML shell is registered before the audited command router: it is
# a credential-free page, not a control attempt, and must not be audited as one.
app.include_router(control_page_router)
app.include_router(controller_lease_router)
app.include_router(control_router)


def main() -> None:
    """Entry point for ``uv run noca-animator``."""
    configure_logging(logging_level=settings.resolved_log_level)
    reload_enabled = settings.ENVIRONMENT == Environment.DEVELOPMENT
    uvicorn.run(
        "animator.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=reload_enabled,
        reload_dirs=["animator", "shared"] if reload_enabled else None,
        proxy_headers=True,
        forwarded_allow_ips=settings.FORWARDED_ALLOW_IPS,
        log_config=None,
        log_level="warning",
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
