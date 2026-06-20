#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""AI Assistant worker process.

Single-worker process that dequeues Arena AI review jobs from
``ai:queue:pending``, calls the OpenAI Responses API, and writes the result
back to ``arena_submissions``.

Entry point: ``main`` (console script ``noca-aiassistant``).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from openai import AuthenticationError, PermissionDeniedError
from secrets_manager import SecretsConfig, SecretsManager

from aiassistant.batch_flusher import run_batch_flusher_loop
from aiassistant.batch_poller import run_batch_poller_loop
from aiassistant.config import settings
from aiassistant.database import create_engine
from aiassistant.db.batch_queries import (
    delete_batch_job,
    get_batch_job_for_submission,
    insert_staged_batch_job,
)
from aiassistant.db.queries import (
    ProblemData,
    clear_submit_to_ai_flag,
    get_problem_data,
    get_submission_for_review,
    get_user_api_key,
    get_user_prefered_language,
    store_ai_review_completed_notification,
    store_ai_review_failed_notification,
    store_ai_review_result,
)
from aiassistant.reaper import run_reaper_loop
from aiassistant.reconciler import run_reconciler_loop
from aiassistant.reviewer import ReviewResult, call_ai_review
from shared.app_logging import configure_logging, log_settings
from shared.db_schema.custom_types import init_encrypted_string
from shared.enumerations import ARENA_AI_BATCH_JOB_TERMINAL_STATUSES, Environment
from shared.services.startup_wait import wait_for_db, wait_for_valkey
from shared.services.valkey_service import (
    LivePauseFlag,
    ValkeyRuntime,
    WorkerClass,
    WorkerCommandType,
    mark_worker_offline,
    publish_worker_last_job,
    reconcile_worker_pause_state,
    resolve_worker_id,
    worker_command_loop,
    worker_presence_loop,
)
from shared.services.valkey_service.queue_ops import (
    complete_arena_ai_review_job,
    dequeue_arena_ai_review_job_id,
    get_ai_review_job_hash,
)

try:
    APP_VERSION = version("noca-aiassistant")
except PackageNotFoundError:
    APP_VERSION = "dev"

logger = logging.getLogger(__name__)


def _prefered_language_instruction(prefered_language: str) -> str:
    """Return the task-content instruction for the user's preferred locale."""
    display_name = {
        "en-US": "English (United States)",
        "pt-BR": "Brazilian Portuguese",
    }.get(prefered_language, "English (United States)")
    return f"Respond in the user's preferred language: {display_name} ({prefered_language})."


async def _process_job(
    submission_id: str,
    engine: object,
    valkey_runtime: ValkeyRuntime,
    use_platform_key: bool | None = None,
) -> None:
    """Fetch job data, call OpenAI, store result, and clean up Valkey state.

    Includes an idempotency guard: if a row already exists in
    ``arena_submission_ai_reviews`` for this submission (e.g. the reaper
    re-enqueued after a crash), the job is skipped and cleaned up without a
    second API call.

    Args:
        submission_id: UUID of the arena_submissions record to review.
        engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Connected Valkey runtime for terminal job cleanup.
        use_platform_key: Decision frozen at request time — ``True`` uses the
            platform key (batch path); ``False`` uses the user's own key
            (online path); ``None`` means the field was absent from the job
            hash (legacy job enqueued before this flag existed) and the
            decision is re-derived dynamically from the user's current state.
    """
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]

    # ---- 1. Fetch submission (idempotency checks) ----
    async with db_engine.begin() as conn:
        sub = await get_submission_for_review(conn, submission_id)

    if sub is None:
        logger.warning("Submission %s not found in DB; cleaning up Valkey job", submission_id)
        await complete_arena_ai_review_job(valkey_runtime, submission_id)
        return

    if sub.already_reviewed:
        logger.info(
            "Submission %s already has an AI review row; cleaning up Valkey job",
            submission_id,
        )
        await complete_arena_ai_review_job(valkey_runtime, submission_id)
        return

    # Batch idempotency guard. A *non-terminal* batch row (including 'staged')
    # means a previous dispatch is genuinely still pending or in flight: skip
    # without creating a duplicate staged row.
    #
    # A *terminal* batch row (failed/expired/cancelled, or completed without a
    # stored review — the already_reviewed check above proves none exists) is a
    # spent attempt. Delete the stale row so a fresh stage can be inserted below.
    async with db_engine.connect() as conn:
        existing_batch = await get_batch_job_for_submission(conn, submission_id)
    if existing_batch is not None:
        if existing_batch.local_status not in ARENA_AI_BATCH_JOB_TERMINAL_STATUSES:
            logger.info(
                "Submission %s has an in-flight batch job %s (status=%s); cleaning up Valkey job",
                submission_id,
                existing_batch.openai_batch_id,
                existing_batch.local_status,
            )
            await complete_arena_ai_review_job(valkey_runtime, submission_id)
            return
        logger.info(
            "Submission %s has a spent terminal batch job %s (status=%s); deleting it to allow re-review",
            submission_id,
            existing_batch.openai_batch_id,
            existing_batch.local_status,
        )
        async with db_engine.begin() as conn:
            await delete_batch_job(conn, submission_id)

    # ---- 2. Resolve API key ----
    # use_platform_key=None means a legacy job hash without the field: fall back
    # to dynamic derivation so pre-deployment inflight jobs are not silently dropped.
    async with db_engine.begin() as conn:
        user_api_key = await get_user_api_key(conn, sub.user_id) if use_platform_key is not True else None
        prefered_language = await get_user_prefered_language(conn, sub.user_id)
        problem: ProblemData = await get_problem_data(conn, sub.problem_id)

    if use_platform_key is None:
        # Legacy job: re-derive the same way the old code did
        api_key = user_api_key or settings.OPENAI_API_KEY
        is_platform_key = (user_api_key is None) and (settings.OPENAI_API_KEY is not None)
    elif use_platform_key:
        is_platform_key = True
        api_key = settings.OPENAI_API_KEY
    else:
        is_platform_key = False
        api_key = user_api_key

    if api_key is None:
        if is_platform_key or use_platform_key is None:
            logger.error(
                "Platform API key not configured for submission %s (user=%s); "
                "set NOCA_AI_OPENAI_API_KEY — cleaning up Valkey job",
                submission_id,
                sub.user_id,
            )
        else:
            logger.error(
                "User API key was removed after job was enqueued for submission %s (user=%s); "
                "cleaning up Valkey job without retry",
                submission_id,
                sub.user_id,
            )
        await complete_arena_ai_review_job(valkey_runtime, submission_id)
        return

    # ---- 3. Dispatch: user key → online (immediate), platform key → staged (windowed) ----
    if is_platform_key:
        await _process_job_batch(
            submission_id,
            db_engine,
            valkey_runtime,
        )
    else:
        await _process_job_online(
            submission_id,
            api_key,
            sub,
            problem,
            db_engine,
            valkey_runtime,
            prefered_language,
        )


async def _process_job_online(
    submission_id: str,
    api_key: str,
    sub: object,
    problem: ProblemData,
    db_engine: object,
    valkey_runtime: ValkeyRuntime,
    prefered_language: str,
) -> None:
    """Handle an AI review job using the synchronous (online) Responses API path.

    Called when the user has their own OpenAI API key. Results are stored
    immediately and all Valkey state for the job is removed.

    Args:
        submission_id: UUID of the ``arena_submissions`` row.
        api_key: User's OpenAI API key.
        sub: ``SubmissionForReview`` dataclass with submission metadata.
        problem: ``ProblemData`` with problem statement and image fields.
        db_engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Connected Valkey runtime for terminal job cleanup.
        prefered_language: User locale for AI review responses.
    """
    from sqlalchemy.ext.asyncio import AsyncEngine

    from aiassistant.db.queries import SubmissionForReview

    db: AsyncEngine = db_engine  # type: ignore[assignment]
    submission: SubmissionForReview = sub  # type: ignore[assignment]

    try:
        result: ReviewResult = await call_ai_review(
            source_code=submission.source_code,
            problem_statement=problem.problem_statement or "",
            language_id=submission.language_id,
            api_key=api_key,
            model=settings.OPENAI_MODEL,
            max_output_tokens=settings.OPENAI_MAX_OUTPUT_TOKENS,
            input_price=settings.OPENAI_INPUT_TOKEN_PRICE,
            output_price=settings.OPENAI_OUTPUT_TOKEN_PRICE,
            is_platform_key=False,
            extra_task_instructions=_prefered_language_instruction(prefered_language),
            image_base64=problem.image_base64,
            image_mime=problem.image_mime,
            image_caption=problem.image_caption,
        )
    except (AuthenticationError, PermissionDeniedError) as exc:
        logger.error(
            "Invalid user API key for submission %s (user=%s): %s — removing without retry",
            submission_id,
            submission.user_id,
            exc,
        )
        async with db.begin() as conn:
            await clear_submit_to_ai_flag(conn, submission_id)
            await store_ai_review_failed_notification(conn, submission, is_user_key=True)
        await complete_arena_ai_review_job(valkey_runtime, submission_id)
        return

    cost_micros = round(result.total_cost * 1_000_000) if result.total_cost is not None else None
    async with db.begin() as conn:
        await store_ai_review_result(
            conn,
            submission_id=submission_id,
            response_text=result.response_text,
            response_at=datetime.now(UTC),
            cost_micros=cost_micros,
            used_platform_key=False,
        )
        await store_ai_review_completed_notification(conn, submission)

    await complete_arena_ai_review_job(valkey_runtime, submission_id)
    logger.info(
        "Online AI review stored for submission %s (tokens=%d, cost=%s)",
        submission_id,
        result.input_tokens + result.output_tokens,
        f"${result.total_cost:.6f}" if result.total_cost is not None else "n/a",
    )


async def _process_job_batch(
    submission_id: str,
    db_engine: object,
    valkey_runtime: ValkeyRuntime,
) -> None:
    """Stage a platform-key job for windowed batch accumulation.

    Inserts a ``staged`` row in ``arena_ai_batch_jobs`` (no OpenAI API calls)
    and removes the job from Valkey inflight. The batch flusher loop will later
    collect all staged rows and submit them as one multi-item OpenAI batch.

    The DB row is inserted before the inflight removal so a crash between the
    two leaves a recoverable state: the idempotency guard in ``_process_job``
    detects the non-terminal (staged) row and skips without creating a duplicate.

    Args:
        submission_id: UUID of the ``arena_submissions`` row.
        db_engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Connected Valkey runtime for terminal job cleanup.
    """
    from sqlalchemy.ext.asyncio import AsyncEngine

    db: AsyncEngine = db_engine  # type: ignore[assignment]

    # (a) Insert staged row — DB is the durable source of truth.
    async with db.begin() as conn:
        await insert_staged_batch_job(conn, submission_id=submission_id)

    logger.info("Submission %s staged for windowed batch flush", submission_id)

    # (b) Remove all Valkey job state — safe even if this crashes: reaper will
    # re-enqueue, and the idempotency guard will detect the staged row and clean up.
    await complete_arena_ai_review_job(valkey_runtime, submission_id)


async def _dequeue_loop(
    engine: object,
    valkey_runtime: ValkeyRuntime,
    stop_event: asyncio.Event,
    pause_flag: LivePauseFlag,
    worker_id: str,
) -> None:
    """Poll the AI review queue and process jobs until stop_event is set.

    Args:
        engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Connected Valkey runtime for queue operations.
        stop_event: Event set by the main worker to request shutdown.
        pause_flag: Run-scoped pause flag reconciled from PostgreSQL.
        worker_id: Stable worker identifier for last-job publishing.
    """
    logger.info("Dequeue loop started")
    while not stop_event.is_set():
        if pause_flag.paused:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                break
            except TimeoutError:
                continue

        try:
            submission_id = await dequeue_arena_ai_review_job_id(valkey_runtime)
        except Exception:
            logger.exception("Failed to dequeue AI review job")
            await asyncio.sleep(settings.AI_POLL_INTERVAL_SECONDS)
            continue

        if submission_id is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.AI_POLL_INTERVAL_SECONDS)
                break  # stop_event was set during sleep
            except TimeoutError:
                continue

        try:
            await publish_worker_last_job(valkey_runtime, worker_class=WorkerClass.AIASSISTANT, worker_id=worker_id)
        except Exception as exc:
            logger.warning("Failed to publish last-job timestamp: %s", exc)

        try:
            job_hash = await get_ai_review_job_hash(valkey_runtime, submission_id)
            if job_hash is None or "use_platform_key" not in job_hash:
                resolved_use_platform_key: bool | None = None  # legacy job — derive dynamically
            else:
                resolved_use_platform_key = job_hash["use_platform_key"].lower() == "true"
            await _process_job(submission_id, engine, valkey_runtime, use_platform_key=resolved_use_platform_key)
        except Exception:
            logger.exception("Failed to process AI review job %s", submission_id)
            # Job remains in inflight — reaper will recover it

    logger.info("Dequeue loop stopped")


async def _restore_startup_pause_state(
    engine: object,
    worker_id: str,
    pause_flag: LivePauseFlag,
) -> None:
    """Restore the AI assistant pause flag from authoritative PostgreSQL state."""
    await reconcile_worker_pause_state(
        lambda: engine.connect(),  # type: ignore[attr-defined]
        worker_class=WorkerClass.AIASSISTANT.value,
        worker_id=worker_id,
        flag=pause_flag,
        logger=logger,
    )


async def run_ai_worker() -> None:
    """Boot resources, run the dequeue loop and reaper, then shut down gracefully."""
    logger.info("*" * 80)
    logger.info(r"  ___  _____    ___          _     _              _   ".center(80, " "))
    logger.info(r" / _ \|_   _|  / _ \        (_)   | |            | |  ".center(80, " "))
    logger.info(r"/ /_\ \ | |   / /_\ \___ ___ _ ___| |_ __ _ _ __ | |_ ".center(80, " "))
    logger.info(r"|  _  | | |   |  _  / __/ __| / __| __/ _` | '_ \| __|".center(80, " "))
    logger.info(r"| | | |_| |_  | | | \__ \__ \ \__ \ || (_| | | | | |_ ".center(80, " "))
    logger.info(r"\_| |_/\___/  \_| |_/___/___/_|___/\__\__,_|_| |_|\__|".center(80, " "))
    logger.info(" " * 80)

    banner = f"Starting AI Assistant worker (version {APP_VERSION}, environment {settings.ENVIRONMENT.value})"
    logger.info(banner.center(80, " "))
    logger.info("| Initializing services |".center(80, "-"))
    log_settings(logger, settings)

    worker_id = resolve_worker_id(settings.AI_WORKER_ID)
    started_at = datetime.now(UTC)

    # ---- SecretsManager: load .env.crypto then initialise ----
    from pathlib import Path

    from dotenv import load_dotenv

    crypto_env_file = Path(settings.AIASSISTANT_CRYPTO_ENV_FILE)
    if not crypto_env_file.exists():
        logger.error("- Crypto environment file not found: %s", crypto_env_file)
        logger.error("  Create the '%s' file before starting the AI Assistant worker.", crypto_env_file)
        sys.exit(1)

    load_dotenv(crypto_env_file, override=False)

    try:
        secrets_config = SecretsConfig.from_environment()
    except ValueError:
        logger.error(
            "SecretsManager environment variables not found or invalid. "
            "Fix the '%s' file before starting the AI Assistant worker.",
            crypto_env_file,
        )
        sys.exit(1)

    secrets_manager = SecretsManager(secrets_config)
    init_encrypted_string(secrets_manager)
    logger.info("- SecretsManager initialized (active_version=%s)", secrets_manager.get_active_version())

    # ---- Wait for infrastructure ----
    await wait_for_db(settings.db_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)
    await wait_for_valkey(settings.valkey_url, timeout_s=settings.STARTUP_TIMEOUT_SECONDS, logger=logger)

    # ---- Database ----
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

    # ---- Signal handling ----
    stop_event = asyncio.Event()
    flush_trigger = asyncio.Event()
    poll_trigger = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown(signame: str) -> None:
        logger.info("Received %s — initiating graceful shutdown", signame)
        stop_event.set()

    def _on_trigger(cmd: str) -> None:
        if cmd == WorkerCommandType.FLUSH_NOW.value:
            flush_trigger.set()
        elif cmd == WorkerCommandType.POLL_NOW.value:
            poll_trigger.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown, sig.name)

    logger.info(
        "- Worker configured (poll=%.1fs, stale=%.0fs, reaper=%.0fs, max_requeue=%d, "
        "batch_poll=%.0fs, batch_window=%.0fs, reconcile=%.0fs)",
        settings.AI_POLL_INTERVAL_SECONDS,
        settings.AI_STALE_THRESHOLD_SECONDS,
        settings.AI_REAPER_INTERVAL_SECONDS,
        settings.AI_MAX_REQUEUE_COUNT,
        settings.AI_BATCH_POLL_INTERVAL_SECONDS,
        5 * settings.AI_BATCH_POLL_INTERVAL_SECONDS,
        settings.AI_RECONCILER_INTERVAL_SECONDS,
    )
    if settings.OPENAI_API_KEY:
        logger.info(
            "- Platform key configured — batch path active (input=%.4f$/1M, output=%.4f$/1M)",
            settings.effective_batch_input_price,
            settings.effective_batch_output_price,
        )
    else:
        logger.warning("- No platform key configured — batch path inactive (user keys only)")
    pause_flag = LivePauseFlag()
    command_secret = settings.WORKER_COMMAND_SECRET
    if command_secret:
        await _restore_startup_pause_state(engine, worker_id, pause_flag)

    extra_loops = []
    if command_secret:
        extra_loops.append(
            worker_command_loop(
                valkey_runtime,
                lambda: engine.connect(),
                worker_class=WorkerClass.AIASSISTANT.value,
                worker_id=worker_id,
                secret=command_secret,
                poll_seconds=settings.AI_WORKER_COMMAND_POLL_SECONDS,
                freshness_seconds=settings.AI_WORKER_COMMAND_FRESHNESS_SECONDS,
                nonce_ttl_seconds=settings.AI_WORKER_COMMAND_NONCE_TTL_SECONDS,
                flag=pause_flag,
                stop_event=stop_event,
                logger=logger,
                on_trigger=_on_trigger,
            )
        )
        logger.info("- Pause/resume and trigger command loop started")
    else:
        logger.info("- Pause/resume command loop disabled (NOCA_WORKER_COMMAND_SECRET unset)")
    logger.info("| AI Assistant worker running |".center(80, "-"))

    try:
        await asyncio.gather(
            _dequeue_loop(engine, valkey_runtime, stop_event, pause_flag, worker_id),
            run_reaper_loop(
                valkey_runtime=valkey_runtime,
                stop_event=stop_event,
                logger=logger,
                stale_threshold_s=settings.AI_STALE_THRESHOLD_SECONDS,
                reaper_interval_s=settings.AI_REAPER_INTERVAL_SECONDS,
                max_requeue_count=settings.AI_MAX_REQUEUE_COUNT,
            ),
            run_batch_flusher_loop(
                engine=engine,
                stop_event=stop_event,
                logger=logger,
                flush_trigger=flush_trigger,
            ),
            run_batch_poller_loop(
                engine=engine,
                valkey_runtime=valkey_runtime,
                stop_event=stop_event,
                logger=logger,
                poll_trigger=poll_trigger,
            ),
            run_reconciler_loop(
                engine=engine,
                valkey_runtime=valkey_runtime,
                stop_event=stop_event,
                logger=logger,
                reconciler_interval_s=settings.AI_RECONCILER_INTERVAL_SECONDS,
                grace_s=settings.AI_RECONCILER_GRACE_SECONDS,
                batch_size=settings.AI_RECONCILER_BATCH_SIZE,
            ),
            worker_presence_loop(
                valkey_runtime,
                worker_class=WorkerClass.AIASSISTANT,
                worker_id=worker_id,
                started_at=started_at,
                interval_seconds=settings.AI_PRESENCE_INTERVAL_SECONDS,
                ttl_seconds=settings.AI_PRESENCE_TTL_SECONDS,
                stop_event=stop_event,
            ),
            *extra_loops,
        )
    finally:
        logger.info("*" * 80)
        await mark_worker_offline(
            valkey_runtime,
            worker_class=WorkerClass.AIASSISTANT,
            worker_id=worker_id,
        )
        await valkey_runtime.stop()
        logger.info("Valkey runtime stopped")
        await engine.dispose()
        logger.info("Database connection pool closed")
        logger.info("AI Assistant worker stopped")
        logger.info("*" * 80)


def _run_ai_worker_process() -> None:
    """Worker process body; also used as the watchfiles hot-reload target."""
    configure_logging(logging_level=settings.resolved_log_level)
    try:
        asyncio.run(run_ai_worker())
    except Exception:
        logger.exception("AI Assistant worker failed")
        sys.exit(1)


def main() -> None:
    """Configure logging and run the AI Assistant worker."""
    import shlex

    if settings.ENVIRONMENT == Environment.DEVELOPMENT:
        try:
            from watchfiles import run_process
        except ImportError:
            raise RuntimeError(
                "watchfiles is required for AI Assistant worker hot-reload in development. "
                "Install it with: uv sync --extra dev"
            ) from None
        worker_command = shlex.join(
            [
                sys.executable,
                "-c",
                "from aiassistant.worker import _run_ai_worker_process; _run_ai_worker_process()",
            ]
        )
        try:
            run_process(
                "aiassistant",
                "shared",
                target=worker_command,
                target_type="command",
            )
        except KeyboardInterrupt:
            logger.info("Development reload supervisor stopped")
    else:
        _run_ai_worker_process()


if __name__ == "__main__":
    main()
