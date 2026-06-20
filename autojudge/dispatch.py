#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Job dispatch.

Routes one dequeued queue job to the correct processing pipeline by its
first-class job kind, owns the idempotency lock, and on failure persists a
terminal FAILED verdict before clearing the job's Valkey state.

Public API
----------
- process_job(...): test/seam entry point — lock + run a web submission
- dispatch_job(...): production entry point — route by JobKind and handle failures
"""

import asyncio
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import docker

from autojudge.arena_submission_job import process_arena_submission_job
from autojudge.config import settings
from autojudge.db import DatabaseAccess, QueuedSubmission
from autojudge.languages import LanguageConfig
from autojudge.metrics import JOBS_COMPLETED_TOTAL, JOBS_DISPATCHED_TOTAL
from autojudge.pool import PoolManager
from autojudge.profiling_job import process_profiling_job
from autojudge.queue_ops import Valkey_Client, remove_from_inflight
from autojudge.submission_job import process_submission_job
from shared.queue_schema import JobKind

logger = logging.getLogger(__name__)


async def process_job(
    *,
    submission: QueuedSubmission,
    db: DatabaseAccess,
    valkey: Valkey_Client,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor | None,
    worker_id: str,
) -> None:
    """
    Acquire the idempotency lock and run one submission through the pipeline.

    This entry point is used directly by tests; production code goes through
    dispatch_job which loads the submission from the DB first.

    Args:
        submission: Loaded submission payload.
        db: Open worker database accessor.
        valkey: Async Valkey client.
        pool_manager: Container pool manager.
        language_registry: Active language registry.
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for Docker SDK calls (may be None in tests).
        worker_id: Stable worker identity string.
    """
    lock_key = f"judge:lock:{submission.judgment_id}"
    acquired = await valkey.set(lock_key, worker_id, nx=True, ex=settings.LOCK_TTL_SECONDS)
    if not acquired:
        with suppress(Exception):
            await remove_from_inflight(valkey, submission.judgment_id)
        return

    clean_up = True
    try:
        await process_submission_job(
            submission=submission,
            db=db,
            valkey=valkey,
            pool_manager=pool_manager,
            language_registry=language_registry,
            docker_client=docker_client,
            executor=executor,  # type: ignore[arg-type]
            worker_id=worker_id,
        )
    except asyncio.CancelledError:
        clean_up = False  # preserve inflight/lock so the reaper can recover
        raise
    except Exception as exc:
        logger.error(f"Unexpected error processing submission {submission.judgment_id}: {str(exc)}")
    finally:
        if clean_up:
            with suppress(Exception):
                await remove_from_inflight(valkey, submission.judgment_id)
            with suppress(Exception):
                await valkey.delete(lock_key)
            with suppress(Exception):
                await valkey.delete(f"{settings.queue_job_hash_prefix}:{submission.judgment_id}")


async def _persist_job_failure(
    db: DatabaseAccess,
    job_id: str,
    job_kind: str,
    exc: Exception,
) -> tuple[bool, str]:
    """Persist a terminal FAILED verdict for a job whose processing raised.

    Selects the right ``db.set_*_failed`` accessor by job kind, handling the
    web→arena ``LookupError`` fallback. On persistence failure it logs the shared
    diagnostic JSON shape and reports that cleanup must not proceed.

    Args:
        db: Open worker database accessor.
        job_id: Job identifier.
        job_kind: JobKind string for the dequeued job.
        exc: The exception raised while processing the job.

    Returns:
        ``(persisted, metric_job_kind)`` — whether the verdict was stored and the
        job kind that should label the completion metric.
    """
    metric_job_kind = job_kind
    try:
        if job_kind == JobKind.PROFILING:
            await db.set_profiling_failed(job_id, f"Internal judge error: {exc}")
        elif job_kind == JobKind.ARENA_SUBMISSION:
            await db.set_arena_judgment_failed(job_id, f"Internal judge error: {exc}\n\n{traceback.format_exc()}")
        else:
            try:
                submission = await db.get_submission_for_judging(job_id)
                await db.set_judgment_failed(
                    job_id,
                    f"Internal judge error: {exc}\n\n{traceback.format_exc()}",
                    contest_start_time=submission.contest_start_time,
                )
            except LookupError:
                await db.get_arena_submission_for_judging(job_id)
                await db.set_arena_judgment_failed(job_id, f"Internal judge error: {exc}\n\n{traceback.format_exc()}")
                metric_job_kind = JobKind.ARENA_SUBMISSION
    except Exception as persist_exc:
        logger.error(
            json.dumps(
                {"job_id": job_id, "error": str(exc), "persist_error": str(persist_exc)},
                indent=2,
            )
        )
        return False, metric_job_kind
    return True, metric_job_kind


async def dispatch_job(
    *,
    job_id: str,
    job_kind: str,
    db: DatabaseAccess,
    valkey: Valkey_Client,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    wid: str,
) -> None:
    """
    Dispatch one queued worker job by its first-class job kind.

    Args:
        job_id: Job identifier (judgment_id or profiling_run_id).
        job_kind: JobKind string determining which pipeline to invoke.
        db: Open worker database accessor.
        valkey: Async Valkey client.
        pool_manager: Container pool manager.
        language_registry: Active language registry.
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for Docker SDK calls.
        wid: Stable worker identity string.
    """
    lock_key = f"judge:lock:{job_id}"
    acquired = await valkey.set(lock_key, wid, nx=True, ex=settings.LOCK_TTL_SECONDS)
    if not acquired:
        await remove_from_inflight(valkey, job_id)
        JOBS_COMPLETED_TOTAL.labels(job_kind=job_kind, outcome="lock_miss").inc()
        return

    JOBS_DISPATCHED_TOTAL.labels(job_kind=job_kind).inc()
    cleanup_job_state = False
    metric_job_kind = job_kind

    try:
        if job_kind == JobKind.PROFILING:
            profiling_run = await db.get_profiling_run_for_judging(job_id)
            await process_profiling_job(
                profiling_run=profiling_run,
                db=db,
                pool_manager=pool_manager,
                language_registry=language_registry,
                docker_client=docker_client,
                executor=executor,
                worker_id=wid,
            )
            cleanup_job_state = True
            JOBS_COMPLETED_TOTAL.labels(job_kind=job_kind, outcome="done").inc()
            return

        if job_kind == JobKind.ARENA_SUBMISSION:
            arena_submission = await db.get_arena_submission_for_judging(job_id)
            await process_arena_submission_job(
                submission=arena_submission,
                db=db,
                valkey=valkey,
                pool_manager=pool_manager,
                language_registry=language_registry,
                docker_client=docker_client,
                executor=executor,
                worker_id=wid,
            )
            cleanup_job_state = True
            JOBS_COMPLETED_TOTAL.labels(job_kind=job_kind, outcome="done").inc()
            return

        try:
            submission = await db.get_submission_for_judging(job_id)
            await process_submission_job(
                submission=submission,
                db=db,
                valkey=valkey,
                pool_manager=pool_manager,
                language_registry=language_registry,
                docker_client=docker_client,
                executor=executor,
                worker_id=wid,
            )
        except LookupError as submission_exc:
            arena_submission = await db.get_arena_submission_for_judging(job_id)
            metric_job_kind = JobKind.ARENA_SUBMISSION
            await process_arena_submission_job(
                submission=arena_submission,
                db=db,
                valkey=valkey,
                pool_manager=pool_manager,
                language_registry=language_registry,
                docker_client=docker_client,
                executor=executor,
                worker_id=wid,
            )
            logger.warning(
                f"Dequeued job {job_id} resolved as Arena submission after legacy submission lookup failed: "
                f"{str(submission_exc)}"
            )
        cleanup_job_state = True
        JOBS_COMPLETED_TOTAL.labels(job_kind=metric_job_kind, outcome="done").inc()
    except LookupError as exc:
        logger.warning(f"Dequeued job {job_id} cannot be processed: {str(exc)}")
        cleanup_job_state = True
        JOBS_COMPLETED_TOTAL.labels(job_kind=metric_job_kind, outcome="lookup_error").inc()
    except asyncio.CancelledError:
        logger.warning("Job processing cancelled; preserving inflight metadata for reaper recovery")
        logger.warning(json.dumps({"job_id": job_id, "job_kind": job_kind}, indent=2))
        raise
    except Exception as exc:
        logger.error(f"Unexpected error processing job {job_id}: {str(exc)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        cleanup_job_state, metric_job_kind = await _persist_job_failure(db, job_id, job_kind, exc)
        if cleanup_job_state:
            JOBS_COMPLETED_TOTAL.labels(job_kind=metric_job_kind, outcome="failed").inc()
    finally:
        if cleanup_job_state:
            await remove_from_inflight(valkey, job_id)
            with suppress(Exception):
                await valkey.delete(lock_key)
            with suppress(Exception):
                await valkey.delete(f"{settings.queue_job_hash_prefix}:{job_id}")
