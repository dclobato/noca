#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Queue reconciliation.

Rebuilds missing Valkey queue state from the database so non-terminal jobs are
never orphaned. We prefer at-least-once semantics: if the database says a job is
not terminal, it must exist in Valkey queue metadata after a reconciliation pass.

``reconcile_queue_state`` runs once before workers start dequeuing
(``phase="Startup"``) and then periodically through ``reconcile_loop``
(``phase="Periodic"``), so a job lost between a producer's DB commit and its
Valkey enqueue is recovered without waiting for a worker restart.

Public API
----------
- reconcile_queue_state(db, valkey, *, phase): one reconciliation pass
- reconcile_loop(shutdown_event, db_engine, valkey): periodic driver
"""

import asyncio
import json
import logging
import time as _time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine

from autojudge.config import settings
from autojudge.db import DatabaseAccess, open_db
from autojudge.queue_ops import Valkey_Client
from autojudge.valkey_decode import decode_valkey_scalar, hash_requeue_count
from shared.enumerations import JudgmentStatus, ProfilingStatus
from shared.queue_schema import JobKind

logger = logging.getLogger(__name__)


@dataclass
class _QueueSnapshot:
    """Mutable snapshot of the current queue id-sets, updated as jobs recover."""

    pending: set[str | None]
    priority: set[str | None]
    profiling: set[str | None]
    inflight: set[str | None]


@dataclass(frozen=True)
class _RecoverableKind:
    """Per-kind reconciliation spec driving the shared rebuild loop.

    Attributes:
        list_jobs: DB accessor returning the recoverable jobs for this kind.
        job_id_of: Extracts the job id (queue key) from a recoverable item.
        active_statuses: Statuses that always force a re-enqueue.
        membership_id_sets: Snapshot id-sets that count as "already queued".
        target_queue_key: Queue key the job is re-pushed onto.
        hash_mapping: Builds the job-hash mapping for the recovered item.
    """

    list_jobs: Callable[[DatabaseAccess], Awaitable[Sequence[Any]]]
    job_id_of: Callable[[Any], str]
    active_statuses: frozenset[Any]
    membership_id_sets: Callable[[_QueueSnapshot], tuple[set[str | None], ...]]
    target_queue_key: str
    hash_mapping: Callable[[Any, str, int], Mapping[str, Any]]


async def _read_id_set(valkey: Valkey_Client, key: str) -> set[str | None]:
    """Return the decoded id-set held by a Valkey list, dropping ``None``."""
    ids = {decode_valkey_scalar(item) for item in await cast(Any, valkey.lrange(key, 0, -1))}
    ids.discard(None)
    return ids


async def _rebuild_kind(
    spec: _RecoverableKind,
    db: DatabaseAccess,
    valkey: Valkey_Client,
    snapshot: _QueueSnapshot,
) -> int:
    """Re-enqueue every recoverable job of one kind that is missing from Valkey.

    Args:
        spec: Per-kind reconciliation spec.
        db: Open worker database accessor.
        valkey: Async Valkey client.
        snapshot: Live queue id-set snapshot, updated in place as jobs recover.

    Returns:
        The number of jobs re-enqueued for this kind.
    """
    recovered = 0
    for item in await spec.list_jobs(db):
        jid = spec.job_id_of(item.payload)
        job_key = f"{settings.queue_job_hash_prefix}:{jid}"
        lock_key = f"judge:lock:{jid}"
        hash_data = await cast(Any, valkey.hgetall(job_key))
        queue_seen = any(jid in id_set for id_set in spec.membership_id_sets(snapshot))
        recover = item.status in spec.active_statuses or not hash_data or not queue_seen
        if not recover:
            continue

        requeue_count = hash_requeue_count(hash_data)
        await cast(Any, valkey.delete(lock_key))
        await cast(Any, valkey.lrem(settings.queue_inflight_key, 0, jid))
        await cast(Any, valkey.zrem(settings.queue_inflight_times_key, jid))
        await cast(Any, valkey.hset(job_key, mapping=dict(spec.hash_mapping(item.payload, jid, requeue_count))))
        await cast(Any, valkey.rpush(spec.target_queue_key, jid))

        if spec.target_queue_key == settings.queue_pending_key:
            snapshot.pending.add(jid)
            snapshot.priority.discard(jid)
        else:
            snapshot.profiling.add(jid)
        snapshot.inflight.discard(jid)
        recovered += 1
    return recovered


def _submission_spec() -> _RecoverableKind:
    return _RecoverableKind(
        list_jobs=lambda db: db.list_recoverable_submission_jobs(),
        job_id_of=lambda payload: payload.judgment_id,
        active_statuses=frozenset({JudgmentStatus.DISPATCHED, JudgmentStatus.JUDGING}),
        membership_id_sets=lambda snap: (snap.pending, snap.priority, snap.inflight),
        target_queue_key=settings.queue_pending_key,
        hash_mapping=lambda payload, jid, requeue_count: {
            "judgment_id": jid,
            "contest_id": payload.contest_id,
            "submission_id": payload.submission_id,
            "is_rejudge": "false",
            "requeue_count": str(requeue_count),
            "job_kind": JobKind.SUBMISSION,
        },
    )


def _profiling_spec() -> _RecoverableKind:
    return _RecoverableKind(
        list_jobs=lambda db: db.list_recoverable_profiling_jobs(),
        job_id_of=lambda payload: payload.profiling_run_id,
        active_statuses=frozenset({ProfilingStatus.DISPATCHED, ProfilingStatus.RUNNING}),
        membership_id_sets=lambda snap: (snap.profiling, snap.inflight),
        target_queue_key=settings.queue_profiling_key,
        hash_mapping=lambda payload, jid, requeue_count: {
            "profiling_run_id": jid,
            "contest_id": payload.contest_id,
            "problem_id": payload.problem_id,
            "language_id": payload.language_id,
            "requeue_count": str(requeue_count),
            "job_kind": JobKind.PROFILING,
        },
    )


def _arena_submission_spec() -> _RecoverableKind:
    return _RecoverableKind(
        list_jobs=lambda db: db.list_recoverable_arena_submission_jobs(),
        job_id_of=lambda payload: payload.judgment_id,
        active_statuses=frozenset({JudgmentStatus.DISPATCHED, JudgmentStatus.JUDGING}),
        membership_id_sets=lambda snap: (snap.pending, snap.priority, snap.inflight),
        target_queue_key=settings.queue_pending_key,
        hash_mapping=lambda payload, jid, requeue_count: {
            "judgment_id": jid,
            "submission_id": payload.submission_id,
            "user_id": payload.user_id,
            "problem_id": payload.problem_id,
            "language_id": payload.language_id,
            "requeue_count": str(requeue_count),
            "job_kind": JobKind.ARENA_SUBMISSION,
        },
    )


async def reconcile_queue_state(db: DatabaseAccess, valkey: Valkey_Client, *, phase: str = "Startup") -> None:
    """Rebuild missing queue state from DB so non-terminal jobs are never orphaned.

    Args:
        db: Open worker database accessor.
        valkey: Async Valkey client.
        phase: Label used in the recovery log line ("Startup" or "Periodic").
    """
    snapshot = _QueueSnapshot(
        pending=await _read_id_set(valkey, settings.queue_pending_key),
        priority=await _read_id_set(valkey, settings.queue_priority_key),
        profiling=await _read_id_set(valkey, settings.queue_profiling_key),
        inflight=await _read_id_set(valkey, settings.queue_inflight_key),
    )

    recovered_submissions = await _rebuild_kind(_submission_spec(), db, valkey, snapshot)
    recovered_profiling = await _rebuild_kind(_profiling_spec(), db, valkey, snapshot)
    recovered_arena_submissions = await _rebuild_kind(_arena_submission_spec(), db, valkey, snapshot)

    if recovered_submissions or recovered_profiling or recovered_arena_submissions:
        logger.warning("%s reconciliation re-enqueued non-terminal jobs", phase)
        logger.warning(
            json.dumps(
                {
                    "recovered_submissions": recovered_submissions,
                    "recovered_profiling": recovered_profiling,
                    "recovered_arena_submissions": recovered_arena_submissions,
                },
                indent=2,
            )
        )


async def reconcile_loop(
    shutdown_event: asyncio.Event,
    db_engine: AsyncEngine,
    valkey: Valkey_Client,
) -> None:
    """Periodically re-enqueue non-terminal jobs missing from the Valkey queue.

    Mirrors the startup reconciliation on an interval so a job that was committed
    to the database by a producer (web/arena) but never enqueued — e.g. the
    process crashed between commit and enqueue — is recovered without waiting for
    an autojudge restart. Each cycle opens its own short-lived DB session.

    Args:
        shutdown_event: Event set when worker shutdown has been requested.
        db_engine: Worker database engine for per-cycle sessions.
        valkey: Async Valkey client.
    """
    logger.info("- Reconciler started (interval=%.0fs)", settings.RECONCILER_INTERVAL_S)

    while not shutdown_event.is_set():
        # Sleep first: startup already ran one reconciliation pass before workers began.
        deadline = _time.monotonic() + settings.RECONCILER_INTERVAL_S
        while not shutdown_event.is_set() and _time.monotonic() < deadline:
            await asyncio.sleep(1.0)
        if shutdown_event.is_set():
            break

        try:
            async with open_db(db_engine) as db:
                await reconcile_queue_state(db, valkey, phase="Periodic")
        except OSError as exc:
            logger.warning("Periodic reconciliation skipped — DB unreachable: %s", exc)
        except Exception as exc:
            logger.error(
                "Periodic reconciliation failed — will retry next interval: %s",
                exc,
                exc_info=True,
            )

    logger.info("Reconciler stopped")
