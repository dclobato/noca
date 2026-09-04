#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Mailer worker process.

Single-worker process that dequeues rendered emails from ``mail:queue:pending``
and delivers them through the shared ``EmailProvider``, pacing itself to the
deployment-wide ``NOCA_MAILER_MAX_PER_MINUTE``.

Entry point: ``main`` (console script ``noca-mailer``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import signal
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from mailer.config import Settings, settings
from mailer.database import create_engine
from mailer.reaper import run_reaper_loop
from shared.app_logging import configure_logging, log_settings
from shared.enumerations import Environment
from shared.queue_schema import MailJob
from shared.services.email_providers import EmailProvider, EmailProviderError
from shared.services.email_service import EmailConfig
from shared.services.startup_wait import wait_for_db, wait_for_valkey
from shared.services.valkey_service import (
    LivePauseFlag,
    ValkeyRuntime,
    WorkerClass,
    mark_worker_offline,
    prune_all_stale_workers,
    publish_worker_last_job,
    reconcile_worker_pause_state,
    resolve_worker_id,
    worker_command_loop,
    worker_presence_loop,
)
from shared.services.valkey_service.queue_ops import complete_mail_job, dequeue_mail_job_id, get_mail_job_hash
from shared.services.worker_heartbeat import heartbeat_loop, remove_heartbeat, touch_heartbeat

try:
    APP_VERSION = version("noca-mailer")
except PackageNotFoundError:
    APP_VERSION = "dev"

logger = logging.getLogger(__name__)


def build_provider() -> EmailProvider:
    """Instantiate the provider this worker delivers through.

    This is the only place in NOCA that builds an SMTP provider: the SMTP
    settings are validated here and nowhere else. With sending disabled or the
    mock provider the worker drains the queue into its log.
    """
    return EmailConfig.from_settings(settings).create_worker_provider()


async def process_job(
    job_id: str,
    valkey_runtime: ValkeyRuntime,
    provider: EmailProvider,
    *,
    job_ttl_seconds: int,
    now: float | None = None,
) -> bool:
    """Deliver one dequeued job and settle its queue state.

    Args:
        job_id: The dequeued job id (already inflight).
        valkey_runtime: Connected Valkey runtime.
        provider: The email provider to deliver through.
        job_ttl_seconds: Maximum age, measured from ``enqueued_at``, a job may
            still be delivered at.
        now: Optional POSIX timestamp override for tests.

    Returns:
        ``True`` when a delivery attempt was made (so the pace applies),
        ``False`` when the job was dropped without contacting the provider.
    """
    job_hash = await get_mail_job_hash(valkey_runtime, job_id)
    if job_hash is None:
        if not getattr(valkey_runtime, "is_available", True):
            # An outage answers None exactly like an expired hash. Leave the
            # job inflight: the reaper retries it once Valkey is back, whereas
            # completing it here would delete an unsent message on reconnect.
            logger.warning("Mail job %s could not be read (Valkey unavailable); leaving it inflight", job_id)
            return False
        # Expired hash (TTL) or a job completed elsewhere: nothing to deliver.
        logger.warning("Mail job %s has no payload (expired or already handled); discarding", job_id)
        await complete_mail_job(valkey_runtime, job_id)
        return False

    try:
        job = MailJob.model_validate(job_hash)
    except ValueError:
        logger.exception("Mail job %s has an unreadable payload; discarding", job_id)
        await complete_mail_job(valkey_runtime, job_id)
        return False

    age = (time.time() if now is None else now) - job.enqueued_at
    if age > job_ttl_seconds:
        # Never deliver late: a credential email nobody delivered in time is
        # dropped, and the sender can trigger a fresh one.
        logger.warning(
            "Dropping mail job %s to %s: queued %.0fs ago, past the %ds TTL", job_id, job.to_email, age, job_ttl_seconds
        )
        await complete_mail_job(valkey_runtime, job_id)
        return False

    try:
        result = await asyncio.to_thread(provider.send, job.to_message())
    except EmailProviderError as exc:
        # Left inflight on purpose: the reaper requeues it after the stale
        # threshold, bounded by the requeue cap.
        logger.warning(
            "Delivery of mail job %s to %s failed (attempt %d): %s", job_id, job.to_email, job.requeue_count + 1, exc
        )
        return True

    if result.success:
        logger.info(
            "Delivered mail job %s to %s via %s (message_id=%s)",
            job_id,
            job.to_email,
            provider.get_provider_name(),
            result.message_id or "N/A",
        )
    else:
        logger.warning("Provider reported failure for mail job %s to %s; not retrying", job_id, job.to_email)
    await complete_mail_job(valkey_runtime, job_id)
    return True


async def _pace(stop_event: asyncio.Event, seconds: float) -> None:
    """Rest between delivery attempts, waking early on shutdown."""
    if seconds <= 0:
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)


async def _dequeue_loop(
    valkey_runtime: ValkeyRuntime,
    provider: EmailProvider,
    stop_event: asyncio.Event,
    pause_flag: LivePauseFlag,
    worker_id: str,
) -> None:
    """Poll the mail queue and deliver jobs until ``stop_event`` is set."""
    logger.info("Dequeue loop started (pace: %d/min)", settings.MAILER_MAX_PER_MINUTE)
    while not stop_event.is_set():
        if pause_flag.paused:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                break
            except TimeoutError:
                continue

        try:
            job_id = await dequeue_mail_job_id(valkey_runtime)
        except Exception:
            logger.exception("Failed to dequeue mail job")
            await asyncio.sleep(settings.MAILER_POLL_INTERVAL_SECONDS)
            continue

        if job_id is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.MAILER_POLL_INTERVAL_SECONDS)
                break
            except TimeoutError:
                continue

        try:
            await publish_worker_last_job(valkey_runtime, worker_class=WorkerClass.MAILER, worker_id=worker_id)
        except Exception as exc:
            logger.warning("Failed to publish last-job timestamp: %s", exc)

        attempted = False
        try:
            attempted = await process_job(
                job_id, valkey_runtime, provider, job_ttl_seconds=settings.EMAIL_QUEUE_JOB_TTL_SECONDS
            )
        except Exception:
            logger.exception("Failed to process mail job %s", job_id)
            # Job remains inflight — the reaper will recover it
            attempted = True
        if attempted:
            await _pace(stop_event, settings.send_interval_seconds)

    logger.info("Dequeue loop stopped")


def describe_delivery(config: Settings) -> str:
    """Return the one-line delivery summary shown in the startup banner.

    Real delivery names the SMTP relay so an operator can tell at a glance
    which server this worker will hand mail to; every other combination is
    mock delivery, where messages are logged and nothing leaves the process.

    Args:
        config: The mailer settings.

    Returns:
        A human-readable summary of how this worker delivers email.
    """
    if config.SEND_EMAIL and config.EMAIL_PROVIDER == "smtp":
        security = "STARTTLS" if config.SMTP_USE_TLS else "no TLS"
        return f"Mail delivery: REAL via SMTP {config.SMTP_SERVER}:{config.SMTP_PORT} ({security})"
    return "Mail delivery: MOCK (messages are logged, nothing is sent)"


async def run_mailer_worker() -> None:
    """Boot resources, run the dequeue loop and reaper, then shut down gracefully."""
    logger.info("*" * 80)
    logger.info(r"___  ___      _ _           ".center(80, " "))
    logger.info(r"|  \/  |     (_) |          ".center(80, " "))
    logger.info(r"| .  . | __ _ _| | ___ _ __ ".center(80, " "))
    logger.info(r"| |\/| |/ _` | | |/ _ \ '__|".center(80, " "))
    logger.info(r"| |  | | (_| | | |  __/ |   ".center(80, " "))
    logger.info(r"\_|  |_/\__,_|_|_|\___|_|   ".center(80, " "))
    logger.info(" " * 80)

    banner = f"Starting mailer worker (version {APP_VERSION}, environment {settings.ENVIRONMENT.value})"
    logger.info(banner.center(80, " "))
    logger.info(describe_delivery(settings).center(80, " "))
    logger.info("| Initializing services |".center(80, "-"))
    log_settings(logger, settings)

    worker_id = resolve_worker_id(settings.MAILER_WORKER_ID)
    started_at = datetime.now(UTC)

    provider = build_provider()
    logger.info(
        "- Email provider: %s (send_email=%s, provider=%s)",
        provider.get_provider_name(),
        settings.SEND_EMAIL,
        settings.EMAIL_PROVIDER,
    )

    # ---- Wait for infrastructure ----
    await wait_for_db(settings.db_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)
    await wait_for_valkey(settings.valkey_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)

    # ---- Database (pause-state reconciliation only) ----
    engine = create_engine(settings.db_url)
    logger.info("- Database connection pool opened")

    # ---- Valkey ----
    valkey_runtime = ValkeyRuntime(
        valkey_url=settings.valkey_url,
        healthcheck_interval_s=settings.VALKEY_HEALTHCHECK_INTERVAL_SECONDS,
    )
    await valkey_runtime.start()
    logger.info("- Valkey runtime started")
    logger.info("- Worker id: %s", worker_id)

    # ---- Heartbeat file ----
    heartbeat_file = Path(settings.MAILER_HEARTBEAT_FILE)
    touch_heartbeat(heartbeat_file)
    logger.info("- Heartbeat file: %s", heartbeat_file)

    # ---- Signal handling ----
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown(signame: str) -> None:
        logger.info("Received %s — initiating graceful shutdown", signame)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown, sig.name)

    # ---- Pause/resume ----
    pause_flag = LivePauseFlag()
    command_secret = settings.WORKER_COMMAND_SECRET
    if command_secret:
        await reconcile_worker_pause_state(
            lambda: engine.connect(),
            worker_class=WorkerClass.MAILER.value,
            worker_id=worker_id,
            flag=pause_flag,
            logger=logger,
        )

    extra_loops = []
    if command_secret:
        extra_loops.append(
            worker_command_loop(
                valkey_runtime,
                lambda: engine.connect(),
                worker_class=WorkerClass.MAILER.value,
                worker_id=worker_id,
                secret=command_secret,
                poll_seconds=settings.MAILER_WORKER_COMMAND_POLL_SECONDS,
                freshness_seconds=settings.MAILER_WORKER_COMMAND_FRESHNESS_SECONDS,
                nonce_ttl_seconds=settings.MAILER_WORKER_COMMAND_NONCE_TTL_SECONDS,
                flag=pause_flag,
                stop_event=stop_event,
                logger=logger,
            )
        )
        logger.info("- Pause/resume command loop started")
    else:
        logger.info("- Pause/resume command loop disabled (NOCA_WORKER_COMMAND_SECRET unset)")

    logger.info("| Mailer worker running |".center(80, "-"))
    try:
        await asyncio.gather(
            _dequeue_loop(valkey_runtime, provider, stop_event, pause_flag, worker_id),
            run_reaper_loop(
                valkey_runtime=valkey_runtime,
                stop_event=stop_event,
                logger=logger,
                stale_threshold_s=settings.MAILER_STALE_THRESHOLD_SECONDS,
                reaper_interval_s=settings.MAILER_REAPER_INTERVAL_SECONDS,
                max_requeue_count=settings.MAILER_MAX_REQUEUE_COUNT,
            ),
            heartbeat_loop(
                heartbeat_file,
                interval_seconds=settings.MAILER_HEARTBEAT_INTERVAL_SECONDS,
                stop_event=stop_event,
            ),
            worker_presence_loop(
                valkey_runtime,
                worker_class=WorkerClass.MAILER,
                worker_id=worker_id,
                started_at=started_at,
                interval_seconds=settings.MAILER_PRESENCE_INTERVAL_SECONDS,
                ttl_seconds=settings.MAILER_PRESENCE_TTL_SECONDS,
                stop_event=stop_event,
            ),
            *extra_loops,
        )
    finally:
        remove_heartbeat(heartbeat_file)
        # A Valkey failure here must not skip the rest of the cleanup.
        with contextlib.suppress(Exception):
            await mark_worker_offline(valkey_runtime, worker_class=WorkerClass.MAILER, worker_id=worker_id)
            await prune_all_stale_workers(valkey_runtime)
        await valkey_runtime.stop()
        await engine.dispose()
        logger.info("Mailer worker stopped")


def _run_mailer_process() -> None:
    """Run the worker process (the watchfiles reload target in development)."""
    configure_logging(logging_level=settings.resolved_log_level)
    try:
        asyncio.run(run_mailer_worker())
    except Exception:
        logger.exception("Mailer worker failed")
        sys.exit(1)


def main() -> None:
    """Console-script entry point (``noca-mailer``)."""
    if settings.ENVIRONMENT == Environment.DEVELOPMENT:
        try:
            from watchfiles import run_process
        except ImportError:
            raise RuntimeError(
                "watchfiles is required for mailer worker hot-reload in development. "
                "Install it with: uv sync --extra dev"
            ) from None
        worker_command = shlex.join(
            [
                sys.executable,
                "-c",
                "from mailer.worker import _run_mailer_process; _run_mailer_process()",
            ]
        )
        try:
            run_process(
                "mailer",
                "shared",
                target=worker_command,
                target_type="command",
            )
        except KeyboardInterrupt:
            logger.info("Development reload supervisor stopped")
    else:
        _run_mailer_process()


if __name__ == "__main__":
    main()
