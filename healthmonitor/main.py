#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Health monitor FastAPI application (default port 8002).

Serves one public page, the uptime dashboard (``/``) with per-service live
statuses and 30-day ECharts heatmaps, an HTMX refresh fragment (``/refresh``),
an uptime data endpoint (``/uptime.json``), and a ``/health`` endpoint for
liveness probes. Two background loops probe the monitored services through
their Valkey presence keys and reap uptime slots older than the retention
window.
"""

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
from jinja2 import ChoiceLoader, FileSystemLoader

from healthmonitor.config import settings
from healthmonitor.error_handlers import register_error_handlers
from healthmonitor.routes.dashboard import router as dashboard_router
from healthmonitor.routes.health import router as health_router
from healthmonitor.services.loops import run_prober_loop, run_reaper_loop
from shared.app_logging import configure_logging
from shared.enumerations import Environment
from shared.services.security_headers import SecurityHeaderSettings, SecurityHeadersMiddleware
from shared.services.startup_wait import wait_for_valkey
from shared.services.valkey_service import ValkeyRuntime
from shared.static_files import RevalidatedStaticFiles

try:
    APP_VERSION = version("noca-healthmonitor")
except PackageNotFoundError:
    APP_VERSION = "dev"

logger = logging.getLogger(__name__)

_HEALTHMON_DIR = Path(__file__).resolve().parent
_SHARED_DIR = _HEALTHMON_DIR.parent / "shared"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Boot Valkey connectivity, templates and background loops."""
    configure_logging(logging_level=settings.resolved_log_level)
    logger.info("*" * 80)
    logger.info(r" _   _            _ _   _    ___  ___            _ _             ".center(80))
    logger.info(r"| | | |          | | | | |   |  \/  |           (_) |            ".center(80))
    logger.info(r"| |_| | ___  __ _| | |_| |__ | .  . | ___  _ __  _| |_ ___  _ __ ".center(80))
    logger.info(r"|  _  |/ _ \/ _` | | __| '_ \| |\/| |/ _ \| '_ \| | __/ _ \| '__|".center(80))
    logger.info(r"| | | |  __/ (_| | | |_| | | | |  | | (_) | | | | | || (_) | |   ".center(80))
    logger.info(r"\_| |_/\___|\__,_|_|\__|_| |_\_|  |_/\___/|_| |_|_|\__\___/|_|   ".center(80))
    logger.info(" " * 80)

    banner = f"Starting Health Monitor (version {APP_VERSION}, environment {settings.ENVIRONMENT.value})"
    logger.info(banner.center(80, " "))
    logger.info("| Initializing services |".center(80, "-"))

    await wait_for_valkey(settings.valkey_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)

    valkey_runtime = ValkeyRuntime(
        valkey_url=settings.valkey_url,
        healthcheck_interval_s=settings.VALKEY_HEALTHCHECK_INTERVAL_SECONDS,
    )
    await valkey_runtime.start()
    app.state.valkey_runtime = valkey_runtime
    logger.info("- Valkey runtime started")

    templates = Jinja2Templates(directory=_HEALTHMON_DIR / "template")
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(_HEALTHMON_DIR / "template")),
            FileSystemLoader(str(_SHARED_DIR / "template")),
        ]
    )
    templates.env.globals["app_version"] = APP_VERSION
    templates.env.globals["brand_name"] = settings.BRAND_NAME
    app.state.templates = templates
    logger.info("- Jinja2 templates initialised (directory=%s)", _HEALTHMON_DIR / "template")

    stop_event = asyncio.Event()
    app.state.loops_stop = stop_event
    app.state.prober_task = asyncio.create_task(
        run_prober_loop(
            valkey_runtime,
            stop_event,
            interval_seconds=settings.PROBE_INTERVAL,
            retention_days=settings.RETENTION_DAYS,
        ),
        name="healthmon-prober",
    )
    app.state.reaper_task = asyncio.create_task(
        run_reaper_loop(
            valkey_runtime,
            stop_event,
            interval_seconds=settings.REAPER_INTERVAL,
        ),
        name="healthmon-reaper",
    )
    logger.info(
        "- Prober started (interval=%ss, retention=%sd); reaper started (interval=%ss)",
        settings.PROBE_INTERVAL,
        settings.RETENTION_DAYS,
        settings.REAPER_INTERVAL,
    )

    logger.info("| Health monitor running |".center(80, "-"))

    yield

    logger.info("*" * 80)

    stop_event.set()
    await asyncio.gather(app.state.prober_task, app.state.reaper_task, return_exceptions=True)
    logger.info("Prober and reaper loops stopped")

    await valkey_runtime.stop()
    logger.info("Valkey runtime stopped")

    logger.info("*" * 80)
    logger.info("Health monitor stopped")
    logger.info("*" * 80)


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

register_error_handlers(app)

# The health monitor has no cookies of its own, so HSTS keys off the environment
# alone rather than a COOKIE_SECURE flag as in web and arena.
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
    RevalidatedStaticFiles(directory=_HEALTHMON_DIR / "static" / "css"),
    name="healthmon_static_css",
)

app.mount(
    "/static/shared-css",
    RevalidatedStaticFiles(directory=_SHARED_DIR / "static" / "css"),
    name="static_shared_css",
)

app.mount(
    "/static/js",
    RevalidatedStaticFiles(directory=_HEALTHMON_DIR / "static" / "js"),
    name="healthmon_static_js",
)

app.mount(
    "/static/shared-js",
    RevalidatedStaticFiles(directory=_SHARED_DIR / "static" / "js"),
    name="static_shared_js",
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

app.include_router(dashboard_router)
app.include_router(health_router)


def main() -> None:
    """Entry point for ``uv run noca-healthmonitor``."""
    configure_logging(logging_level=settings.resolved_log_level)
    reload_enabled = settings.ENVIRONMENT == Environment.DEVELOPMENT
    uvicorn.run(
        "healthmonitor.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=reload_enabled,
        reload_dirs=["healthmonitor", "shared"] if reload_enabled else None,
        proxy_headers=True,
        forwarded_allow_ips=settings.FORWARDED_ALLOW_IPS,
        log_config=None,
        log_level="warning",
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
