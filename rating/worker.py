#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Rating worker process.

Single-replica worker that owns the Arena rating recomputation loops. Running
the loops in a dedicated process (instead of inside every Arena instance)
guarantees exactly one set of cycles regardless of how many Arena replicas sit
behind the load balancer.

The next scheduled cycle timestamp is published to Valkey under
``arena:rating:next_update`` so any number of Arena instances can render a
consistent footer without sharing process memory.

Entry point: ``main`` (console script ``noca-rating``).
"""

import asyncio
import logging
import signal
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from rating.config import settings
from rating.database import create_engine, create_session_factory
from rating.loops import (
    run_affiliation_rating_loop,
    run_problem_rating_loop,
    run_problem_stats_loop,
    run_user_rating_loop,
)
from shared.app_logging import configure_logging, log_settings
from shared.enumerations import Environment
from shared.services.arena_rating import (
    NEXT_RATING_UPDATE_KEY,
    RATING_AFFILIATION_FACTOR_KEY,
    RATING_INTERVAL_TEXT_KEY,
    format_rating_interval,
)
from shared.services.startup_wait import wait_for_db
from shared.services.valkey_service import (
    ValkeyRuntime,
    WorkerClass,
    mark_worker_offline,
    publish_worker_last_job,
    resolve_worker_id,
    worker_presence_loop,
)

try:
    APP_VERSION = version("noca-rating")
except PackageNotFoundError:
    APP_VERSION = "dev"

logger = logging.getLogger(__name__)


def _make_next_update_callback(
    valkey_runtime: ValkeyRuntime,
    pending_tasks: set[asyncio.Task[None]],
) -> Any:
    """Build a sync callback that mirrors the next-update deadline into Valkey.

    The rating loops invoke the callback synchronously from within the running
    event loop; the Valkey write itself is async, so it is scheduled as a task.

    Args:
        valkey_runtime: Runtime used to publish the next-update timestamp.
        pending_tasks: Set retaining task references until they complete.

    Returns:
        A callable accepting ``datetime | None`` (None clears the key).
    """

    async def _publish(next_update: datetime | None) -> None:
        await _publish_rating_metadata(valkey_runtime)
        if next_update is None:
            await valkey_runtime.delete(NEXT_RATING_UPDATE_KEY)
        else:
            await valkey_runtime.set(
                NEXT_RATING_UPDATE_KEY,
                next_update.isoformat(),
                ex=settings.RATING_INTERVAL + 600,
            )

    def _callback(next_update: datetime | None) -> None:
        task = asyncio.create_task(_publish(next_update))
        pending_tasks.add(task)
        task.add_done_callback(pending_tasks.discard)

    return _callback


async def _publish_rating_metadata(valkey_runtime: ValkeyRuntime) -> None:
    """Publish active rating display metadata to Valkey for Arena instances.

    Args:
        valkey_runtime: Runtime used to publish rating scheduler metadata.
    """
    interval_text = format_rating_interval(settings.RATING_INTERVAL)
    if interval_text is not None:
        await valkey_runtime.set(
            RATING_INTERVAL_TEXT_KEY,
            interval_text,
            ex=settings.RATING_INTERVAL + 600,
        )
    await valkey_runtime.set(
        RATING_AFFILIATION_FACTOR_KEY,
        str(settings.AFFILIATION_RATING_FACTOR),
        ex=settings.RATING_INTERVAL + 600,
    )


async def run_rating_worker() -> None:
    """Boot resources, run the three rating loops, and shut down gracefully."""
    worker_id = resolve_worker_id(settings.RATING_WORKER_ID)
    started_at = datetime.now(UTC)
    logger.info("*" * 80)
    logger.info(r" _   _                  ______      _   _             ".center(80, " "))
    logger.info(r"| \ | |                 | ___ \    | | (_)            ".center(80, " "))
    logger.info(r"|  \| | ___   ___ __ _  | |_/ /__ _| |_ _ _ __   __ _ ".center(80, " "))
    logger.info(r"| . ` |/ _ \ / __/ _` | |    // _` | __| | '_ \ / _` |".center(80, " "))
    logger.info(r"| |\  | (_) | (_| (_| | | |\ \ (_| | |_| | | | | (_| |".center(80, " "))
    logger.info(r"\_| \_/\___/ \___\__,_| \_| \_\__,_|\__|_|_| |_|\__, |".center(80, " "))
    logger.info(r"                                                 __/ |".center(80, " "))
    logger.info(r"                                                |___/ ".center(80, " "))
    logger.info(" " * 80)

    banner = f"Starting Rating worker (version {APP_VERSION}, environment {settings.ENVIRONMENT.value})"
    logger.info(banner.center(80, " "))
    logger.info("| Initializing services |".center(80, "-"))
    log_settings(logger, settings)

    await wait_for_db(settings.db_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)

    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    logger.info("- Database connection pool opened")

    valkey_runtime = ValkeyRuntime(
        valkey_url=settings.valkey_url,
        healthcheck_interval_s=settings.VALKEY_HEALTHCHECK_INTERVAL_SECONDS,
    )
    await valkey_runtime.start()
    logger.info("- Valkey runtime started")
    logger.info("- Worker id: %s", worker_id)
    await _publish_rating_metadata(valkey_runtime)
    logger.info(
        "- Rating metadata published (interval_key=%s, factor_key=%s)",
        RATING_INTERVAL_TEXT_KEY,
        RATING_AFFILIATION_FACTOR_KEY,
    )
    logger.info(
        "- Rating loops configured (interval=%ss, affiliation_factor=%.1f)",
        settings.RATING_INTERVAL,
        settings.AFFILIATION_RATING_FACTOR,
    )

    stop_event = asyncio.Event()
    problem_done = asyncio.Event()
    user_done = asyncio.Event()
    pending_tasks: set[asyncio.Task[None]] = set()

    loop = asyncio.get_running_loop()

    def _request_shutdown(signame: str) -> None:
        logger.info("Received %s — initiating graceful shutdown", signame)
        stop_event.set()
        problem_done.set()  # unblock user loop if waiting on problem_done
        user_done.set()  # unblock affiliation loop if waiting on user_done

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown, sig.name)

    next_update_callback = _make_next_update_callback(valkey_runtime, pending_tasks)

    async def _on_rating_cycle_start() -> None:
        await publish_worker_last_job(valkey_runtime, worker_class=WorkerClass.RATING, worker_id=worker_id)

    logger.info(
        "- Rating loops starting (interval=%ss, run_immediately=%s, affiliation_factor=%.1f)",
        settings.RATING_INTERVAL,
        settings.COMPUTE_RATINGS_ON_STARTUP,
        settings.AFFILIATION_RATING_FACTOR,
    )
    logger.info("| Rating worker running |".center(80, "-"))

    try:
        await asyncio.gather(
            run_problem_rating_loop(
                session_factory=session_factory,
                interval_seconds=settings.RATING_INTERVAL,
                stop_event=stop_event,
                logger=logger,
                problem_done=problem_done,
                run_immediately=settings.COMPUTE_RATINGS_ON_STARTUP,
                next_update_callback=next_update_callback,
                on_cycle_start=_on_rating_cycle_start,
            ),
            run_user_rating_loop(
                session_factory=session_factory,
                interval_seconds=settings.RATING_INTERVAL,
                stop_event=stop_event,
                logger=logger,
                problem_done=problem_done,
                user_done=user_done,
            ),
            run_affiliation_rating_loop(
                session_factory=session_factory,
                stop_event=stop_event,
                logger=logger,
                user_done=user_done,
                f=settings.AFFILIATION_RATING_FACTOR,
            ),
            run_problem_stats_loop(
                session_factory=session_factory,
                interval_seconds=settings.STATS_INTERVAL,
                stop_event=stop_event,
                logger=logger,
                run_immediately=settings.COMPUTE_RATINGS_ON_STARTUP,
            ),
            worker_presence_loop(
                valkey_runtime,
                worker_class=WorkerClass.RATING,
                worker_id=worker_id,
                started_at=started_at,
                interval_seconds=settings.RATING_PRESENCE_INTERVAL_SECONDS,
                ttl_seconds=settings.RATING_PRESENCE_TTL_SECONDS,
                stop_event=stop_event,
            ),
        )
    finally:
        logger.info("*" * 80)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        await valkey_runtime.delete(NEXT_RATING_UPDATE_KEY)
        await valkey_runtime.delete(RATING_INTERVAL_TEXT_KEY)
        await valkey_runtime.delete(RATING_AFFILIATION_FACTOR_KEY)
        await mark_worker_offline(
            valkey_runtime,
            worker_class=WorkerClass.RATING,
            worker_id=worker_id,
        )
        await valkey_runtime.stop()
        logger.info("Valkey runtime stopped")
        await engine.dispose()
        logger.info("Database connection pool closed")
        logger.info("*" * 80)
        logger.info("Rating worker stopped")
        logger.info("*" * 80)


def _run_rating_process() -> None:
    """Worker process body; also used as the watchfiles hot-reload target."""
    import sys

    configure_logging(logging_level=settings.resolved_log_level)
    try:
        asyncio.run(run_rating_worker())
    except Exception:
        logger.exception("Rating worker failed")
        sys.exit(1)


def main() -> None:
    """Configure logging and run the rating worker."""
    import shlex
    import sys

    if settings.ENVIRONMENT == Environment.DEVELOPMENT:
        try:
            from watchfiles import run_process
        except ImportError:
            raise RuntimeError(
                "watchfiles is required for rating-worker hot-reload in development. "
                "Install it with: uv sync --extra dev"
            ) from None
        worker_command = shlex.join(
            [
                sys.executable,
                "-c",
                "from rating.worker import _run_rating_process; _run_rating_process()",
            ]
        )
        try:
            run_process(
                "rating",
                "shared",
                target=worker_command,
                target_type="command",
            )
        except KeyboardInterrupt:
            logger.info("Development reload supervisor stopped")
    else:
        _run_rating_process()


if __name__ == "__main__":
    main()
