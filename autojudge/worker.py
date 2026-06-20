#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Async judge worker main loop.

Entry point: run_worker()

Architecture
------------
Fixed pool of worker_concurrency async coroutines, each independently
consuming from the Valkey queue in a tight loop. Each coroutine IS one
concurrency slot — natural backpressure without semaphores.

    asyncio.gather(
        _worker_loop(slot=0, ...),
        _worker_loop(slot=1, ...),
        ...
        reaper_loop(...),
        heartbeat_loop(...),
    )
"""

import asyncio
import contextlib
import logging
import signal
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

import docker
import valkey.asyncio as aiovalkey
from sqlalchemy.exc import ProgrammingError as _SQLAlchemyProgrammingError

from autojudge.config import settings
from autojudge.db import DatabaseAccess, create_worker_engine, open_db
from autojudge.dispatch import dispatch_job as _dispatch_job
from autojudge.dispatch import process_job as _process_job
from autojudge.heartbeat import heartbeat_loop, remove_heartbeat_file, touch_heartbeat_file, worker_id
from autojudge.image_sync import assert_required_images_present, sync_registry_images_from_settings
from autojudge.languages import LanguageConfig
from autojudge.metrics import (
    WORKER_SLOTS_ACTIVE,
    WORKER_START_TIMESTAMP,
    start_metrics_server,
    update_pool_gauges,
    update_queue_depths,
)
from autojudge.pool import PoolManager
from autojudge.queue_ops import (
    Valkey_Client,
    dequeue_job_id,
    get_job_kind,
    remove_from_inflight,
)
from autojudge.reaper import reaper_loop as _reaper_loop_impl
from autojudge.reconcile import reconcile_loop as _reconcile_loop
from autojudge.reconcile import reconcile_queue_state as _reconcile_queue_state
from shared.app_logging import log_settings
from shared.language_registry import registry_from_rows
from shared.services.startup_wait import wait_for_db, wait_for_valkey
from shared.services.valkey_service.constants import (
    QUEUE_INFLIGHT_KEY,
    QUEUE_PENDING_KEY,
    QUEUE_PRIORITY_KEY,
    QUEUE_PROFILING_KEY,
)
from shared.services.valkey_service.worker_commands import (
    LivePauseFlag,
    reconcile_worker_pause_state,
    worker_command_loop,
)
from shared.services.valkey_service.worker_presence import (
    WorkerClass,
    mark_worker_offline,
    publish_worker_last_job,
    worker_presence_loop,
)

logger = logging.getLogger(__name__)

# Re-exported for tests and scripts that import these from ``autojudge.worker``.
__all__ = [
    "run_worker",
    "main",
    "_process_job",
    "_dispatch_job",
    "_reconcile_queue_state",
    "_reconcile_loop",
]

try:
    APP_VERSION = version("noca-autojudge")
except PackageNotFoundError:
    APP_VERSION = "dev"

_LANGUAGE_REGISTRY_RETRY_INTERVAL_S = 5
_IDLE_DEQUEUE_SLEEP_S = 0.25


async def _load_language_registry_when_ready(
    db: DatabaseAccess,
    shutdown_event: asyncio.Event,
) -> dict[str, LanguageConfig]:
    """
    Poll the database until at least one active language is available.

    Args:
        db: Open worker database accessor.
        shutdown_event: Event set when worker shutdown has been requested.

    Returns:
        Mapping of language IDs to runtime language configuration.

    Raises:
        RuntimeError: If shutdown is requested before any active language exists.
    """
    attempt = 1
    while True:
        try:
            rows = await db.list_languages()
        except _SQLAlchemyProgrammingError:
            raise RuntimeError(
                "Failed to query the 'languages' table — the database schema may not have been migrated. "
                "Run 'alembic upgrade head' before starting the worker."
            ) from None
        if rows:
            return registry_from_rows(rows)

        logger.info(
            f"Waiting for active languages in database before starting worker. "
            f"Attempt {attempt}. Retry in {_LANGUAGE_REGISTRY_RETRY_INTERVAL_S} seconds"
        )

        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=_LANGUAGE_REGISTRY_RETRY_INTERVAL_S,
            )
        except TimeoutError:
            attempt += 1
            continue

        raise RuntimeError("Worker shutdown requested before languages were available")


async def _worker_loop(
    slot: int,
    shutdown_event: asyncio.Event,
    db_engine: Any,
    valkey: Valkey_Client,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    wid: str,
    pause_flag: LivePauseFlag,
) -> None:
    """
    Single worker coroutine — runs until shutdown_event is set.

    Uses a separate DB connection per slot (via open_db) for the duration of
    each job; connections are returned to the pool between jobs.

    Args:
        slot: Zero-based slot index (for logging).
        shutdown_event: Event that signals the loop to stop.
        db_engine: Shared AsyncEngine.
        valkey: Async Valkey client.
        pool_manager: Container pool manager.
        language_registry: Active language registry.
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for Docker SDK calls.
        wid: Stable worker identity string.
        pause_flag: Live pause state shared by all worker slots.
    """
    logger.info("- Worker slot %s started", slot)

    while not shutdown_event.is_set():
        if pause_flag.paused:
            await asyncio.sleep(1)
            continue

        try:
            job_id = await dequeue_job_id(valkey)
        except Exception as exc:
            logger.error(f"Worker slot {slot}: Valkey dequeue error — pausing 5s: {str(exc)}")
            await asyncio.sleep(5)
            continue

        if job_id is None:
            await asyncio.sleep(_IDLE_DEQUEUE_SLEEP_S)
            continue

        try:
            await publish_worker_last_job(valkey, worker_class=WorkerClass.AUTOJUDGE, worker_id=wid)
        except Exception as exc:
            logger.warning("Worker slot %s: failed to publish last-job timestamp: %s", slot, exc)

        try:
            await valkey.zadd(settings.queue_inflight_times_key, {job_id: _time.time()})
        except Exception as exc:
            logger.warning(f"Worker slot {slot}: failed to record inflight timestamp for job {job_id}: {str(exc)}")

        try:
            kind = await get_job_kind(valkey, job_id)
        except Exception as exc:
            logger.warning(f"Worker slot {slot}: failed to read job kind for {job_id}: {str(exc)}")
            await remove_from_inflight(valkey, job_id)
            continue

        async with open_db(db_engine) as db:
            await _dispatch_job(
                job_id=job_id,
                job_kind=kind,
                db=db,
                valkey=valkey,
                pool_manager=pool_manager,
                language_registry=language_registry,
                docker_client=docker_client,
                executor=executor,
                wid=wid,
            )

    logger.info(f"Worker slot {slot} stopped")


async def _restore_startup_pause_state(
    db_engine: Any,
    worker_id: str,
    pause_flag: LivePauseFlag,
) -> None:
    """Restore the durable pause state before worker slots begin dequeuing."""
    await reconcile_worker_pause_state(
        lambda: db_engine.connect(),
        worker_class=WorkerClass.AUTOJUDGE.value,
        worker_id=worker_id,
        flag=pause_flag,
        logger=logger,
    )


async def _metrics_update_loop(
    shutdown_event: asyncio.Event,
    valkey: Valkey_Client,
    pool_manager: PoolManager,
) -> None:
    """
    Poll pool availability and queue depths every 15 s and update Prometheus gauges.

    Args:
        shutdown_event: Signals when the worker is shutting down.
        valkey: Async Valkey client for llen calls.
        pool_manager: Container pool manager for pool_status().
    """
    _UPDATE_INTERVAL_S = 15.0

    while not shutdown_event.is_set():
        try:
            update_pool_gauges(pool_manager.pool_status())
            update_queue_depths(
                {
                    "pending": int(await cast(Any, valkey.llen(QUEUE_PENDING_KEY)) or 0),
                    "priority": int(await cast(Any, valkey.llen(QUEUE_PRIORITY_KEY)) or 0),
                    "profiling": int(await cast(Any, valkey.llen(QUEUE_PROFILING_KEY)) or 0),
                    "inflight": int(await cast(Any, valkey.llen(QUEUE_INFLIGHT_KEY)) or 0),
                }
            )
        except Exception as exc:
            logger.warning(f"Metrics gauge update failed: {exc}")

        deadline = _time.monotonic() + _UPDATE_INTERVAL_S
        while not shutdown_event.is_set() and _time.monotonic() < deadline:
            await asyncio.sleep(1.0)


async def run_worker() -> None:
    """
    Main async entry point for the judge worker process.

    Boots all shared resources (Docker client, Valkey connection, DB engine,
    container pool), starts worker coroutines, and runs until SIGTERM/SIGINT.
    """
    wid = worker_id()
    started_at = datetime.now(UTC)
    logger.info("*" * 80)
    logger.info(r"  ___        _            ___           _            ".center(80, " "))
    logger.info(r" / _ \      | |          |_  |         | |           ".center(80, " "))
    logger.info(r"/ /_\ \_   _| |_ ___       | |_   _  __| | __ _  ___ ".center(80, " "))
    logger.info(r"|  _  | | | | __/ _ \      | | | | |/ _` |/ _` |/ _ \\".center(80, " "))
    logger.info(r"| | | | |_| | || (_) | /\__/ / |_| | (_| | (_| |  __/".center(80, " "))
    logger.info(r"\_| |_/\__,_|\__\___/  \____/ \__,_|\__,_|\__, |\___|".center(80, " "))
    logger.info(r"                                           __/ |     ".center(80, " "))
    logger.info(r"                                          |___/      ".center(80, " "))
    logger.info(" " * 80)

    banner = f"Starting Auto Judge (version {APP_VERSION}, environment {settings.ENVIRONMENT.value})"
    logger.info(banner.center(80, " "))
    logger.info("-" * 80)
    logger.info("Problem test case directory: %s", settings.PROBLEM_TESTCASE_DIR)
    logger.info("| Initializing services |".center(80, "-"))
    log_settings(logger, settings)
    logger.info("- Worker id: %s", wid)

    assert settings.LOCK_TTL_SECONDS > settings.REAPER_STALE_THRESHOLD_MINUTES * 60, (
        f"judge_lock_ttl_seconds ({settings.LOCK_TTL_SECONDS}s) must be greater than "
        f"reaper_stale_threshold_minutes * 60 "
        f"({settings.REAPER_STALE_THRESHOLD_MINUTES * 60:.0f}s). "
        "Increase NOCA_JUDGE_LOCK_TTL_SECONDS or decrease NOCA_JUDGE_REAPER_STALE_THRESHOLD_MINUTES."
    )

    await wait_for_db(settings.db_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)
    await wait_for_valkey(settings.valkey_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)

    shutdown_event = asyncio.Event()
    docker_client: docker.DockerClient | None = None
    valkey: Valkey_Client | None = None
    db_engine: Any | None = None
    pool_manager: PoolManager | None = None
    metrics_server: asyncio.Server | None = None
    remove_heartbeat_file()
    touch_heartbeat_file()

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="docker-sdk")

    try:
        docker_client = await loop.run_in_executor(
            executor,
            lambda: docker.DockerClient(base_url=settings.DOCKER_BASE_URL, timeout=60),
        )

        db_engine = create_worker_engine()
        async with open_db(db_engine) as db:
            language_registry = await _load_language_registry_when_ready(db, shutdown_event)
            language_registry = await sync_registry_images_from_settings(
                db=db,
                docker_client=docker_client,
                executor=executor,
                language_registry=language_registry,
            )
        await assert_required_images_present(docker_client, executor, language_registry)

        valkey = await aiovalkey.from_url(  # type: ignore[no-untyped-call]
            settings.valkey_url,
            encoding="utf-8",
            decode_responses=False,
            socket_timeout=None,
            socket_connect_timeout=10,
        )

        async with open_db(db_engine) as db:
            await _reconcile_queue_state(db, valkey, phase="Startup")

        pause_flag = LivePauseFlag()
        command_secret = settings.WORKER_COMMAND_SECRET
        if command_secret:
            await _restore_startup_pause_state(db_engine, wid, pause_flag)

        pool_manager = PoolManager(language_registry)

        if settings.PRE_WARM_CONTAINERS:
            logger.info("- Container pre-warming enabled; pools will be warmed on first use per language")
        else:
            logger.info("- Container pre-warming disabled; run containers will be created on demand")

        WORKER_SLOTS_ACTIVE.set(settings.WORKER_CONCURRENCY)
        WORKER_START_TIMESTAMP.set(_time.time())

        metrics_server = None
        if settings.METRICS_ENABLED:
            metrics_server = await start_metrics_server(settings.METRICS_PORT)

        def _request_shutdown(signame: str) -> None:
            logger.info(f"Received {signame} — initiating graceful shutdown")
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _request_shutdown, sig.name)

        worker_tasks = [
            asyncio.create_task(
                _worker_loop(
                    slot=i,
                    shutdown_event=shutdown_event,
                    db_engine=db_engine,
                    valkey=valkey,
                    pool_manager=pool_manager,
                    language_registry=language_registry,
                    docker_client=docker_client,
                    executor=executor,
                    wid=wid,
                    pause_flag=pause_flag,
                ),
                name=f"worker-slot-{i}",
            )
            for i in range(settings.WORKER_CONCURRENCY)
        ]

        reaper_task = asyncio.create_task(_reaper_loop_impl(shutdown_event, valkey), name="reaper")
        logger.info("- Reaper started")
        reconciler_task = asyncio.create_task(_reconcile_loop(shutdown_event, db_engine, valkey), name="reconciler")
        heartbeat_task = asyncio.create_task(heartbeat_loop(shutdown_event), name="heartbeat")
        presence_task = asyncio.create_task(
            worker_presence_loop(
                valkey,
                worker_class=WorkerClass.AUTOJUDGE,
                worker_id=wid,
                started_at=started_at,
                interval_seconds=settings.PRESENCE_INTERVAL_SECONDS,
                ttl_seconds=settings.PRESENCE_TTL_SECONDS,
                stop_event=shutdown_event,
            ),
            name="worker-presence",
        )

        all_tasks: list[asyncio.Task[None]] = [
            *worker_tasks,
            reaper_task,
            reconciler_task,
            heartbeat_task,
            presence_task,
        ]
        if command_secret:
            all_tasks.append(
                asyncio.create_task(
                    worker_command_loop(
                        valkey,
                        lambda: db_engine.connect(),
                        worker_class=WorkerClass.AUTOJUDGE.value,
                        worker_id=wid,
                        secret=command_secret,
                        poll_seconds=settings.WORKER_COMMAND_POLL_SECONDS,
                        freshness_seconds=settings.WORKER_COMMAND_FRESHNESS_SECONDS,
                        nonce_ttl_seconds=settings.WORKER_COMMAND_NONCE_TTL_SECONDS,
                        flag=pause_flag,
                        stop_event=shutdown_event,
                        logger=logger,
                    ),
                    name="worker-command",
                )
            )
            logger.info("- Worker command listener started")
        else:
            logger.info("- Worker command listener disabled: WORKER_COMMAND_SECRET is not configured")

        if settings.METRICS_ENABLED:
            all_tasks.append(
                asyncio.create_task(_metrics_update_loop(shutdown_event, valkey, pool_manager), name="metrics-update")
            )

        logger.info("- Worker '%s' running. Concurrency: %s", wid, settings.WORKER_CONCURRENCY)
        logger.info("| Autojudge running |".center(80, "-"))
        await asyncio.gather(*all_tasks)

    except Exception as exc:
        logger.error(f"Worker task failed unexpectedly: {exc}")
        raise
    finally:
        shutdown_event.set()
        logger.info("Shutting down worker resources...")
        if metrics_server is not None:
            metrics_server.close()
            await metrics_server.wait_closed()
        if pool_manager is not None:
            await pool_manager.shutdown()
        if valkey is not None:
            with contextlib.suppress(Exception):
                await mark_worker_offline(
                    valkey,
                    worker_class=WorkerClass.AUTOJUDGE,
                    worker_id=wid,
                )
            await cast(Any, valkey).aclose()
        if db_engine is not None:
            await db_engine.dispose()
        if docker_client is not None:
            docker_client.close()
        executor.shutdown(wait=False)
        remove_heartbeat_file()
        logger.info("Worker shutdown complete")


def _run_worker_process() -> None:
    """Worker process body; also used as the watchfiles hot-reload subprocess target."""
    import sys

    from shared.app_logging import configure_logging

    configure_logging(logging_level=settings.resolved_log_level)
    try:
        asyncio.run(run_worker())
    except Exception:
        # run_worker() already logged the error; exit without reprinting the traceback.
        sys.exit(1)


def main() -> None:
    """Configure logging and run the worker."""
    import shlex
    import sys

    from shared.enumerations import Environment

    if settings.ENVIRONMENT == Environment.DEVELOPMENT:
        try:
            from watchfiles import run_process
        except ImportError:
            raise RuntimeError(
                "watchfiles is required for autojudge hot-reload in development. Install it with: uv sync --extra dev"
            ) from None
        worker_command = shlex.join(
            [
                sys.executable,
                "-c",
                "from autojudge.worker import _run_worker_process; _run_worker_process()",
            ]
        )
        try:
            run_process(
                "autojudge",
                "shared",
                target=worker_command,
                target_type="command",
            )
        except KeyboardInterrupt:
            logger.info("Development reload supervisor stopped")
    else:
        _run_worker_process()


if __name__ == "__main__":
    main()
