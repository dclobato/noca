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

Reconciliation rebuilds *missing* queue state only. It never reclaims work that
is already claimed, because doing so hands the same job to a second worker whose
dispatch reset deletes the first worker's partial results. The discriminator is
observed queue membership at the instant we act, never the database status: a
job's status is read before the queue is inspected, so a ``DISPATCHED``/``JUDGING``
row says nothing about whether a worker holds it right now.

An initial queue snapshot provides a conservative fast path: membership can
suppress recovery for one pass but can never cause a mutation. Every repair or
enqueue decision runs through ``queue_ops.reconcile_job_state`` in one Lua
script, so it cannot interleave with a worker's own atomic dequeue. Outcomes:

- in ``judge:queue:inflight`` — left alone; reclaiming stale inflight work is the
  reaper's job, bounded by its stale threshold. This also covers the window
  between the dequeue script's ``LPUSH`` and the worker's ``SET NX`` lock, when a
  live job legitimately holds no lock yet.
- lock held — left alone; the worker is still finishing its cleanup.
- already queued — never pushed a second time; a missing job hash is repaired.
- absent everywhere and unlocked — genuinely orphaned, so rebuild and enqueue.

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
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine

from autojudge.config import settings
from autojudge.db import DatabaseAccess, open_db
from autojudge.metrics import RECONCILE_SKIPPED_TOTAL
from autojudge.queue_ops import Valkey_Client, delete_job_state, reconcile_job_state
from autojudge.valkey_decode import decode_valkey_scalar, hash_requeue_count
from shared.queue_schema import JobKind

logger = logging.getLogger(__name__)


_SKIP_OUTCOMES = ("inflight", "locked", "queued", "hash_repaired")


@dataclass
class _QueueSnapshot:
    """Conservative queue snapshot used only to skip known queued work cheaply."""

    queued: set[str]
    inflight: set[str]


@dataclass
class _KindOutcome:
    """Per-kind tally of one reconciliation pass, keyed by script outcome."""

    recovered: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    terminalized: int = 0


@dataclass(frozen=True)
class _RecoverableKind:
    """Per-kind reconciliation spec driving the shared rebuild loop.

    Attributes:
        job_kind: ``JobKind`` value, used as the metrics label for this kind.
        list_jobs: DB accessor returning the recoverable jobs for this kind.
        job_id_of: Extracts the job id (queue key) from a recoverable item.
        target_queue_key: Queue key the job is re-pushed onto.
        hash_mapping: Builds the job-hash mapping for the recovered item.
        terminalize_exhausted: Optional hook invoked when the reaper left a
            ``reaper_dropped=true`` tombstone on the job hash. It must move the
            database row to a terminal state; the tombstone and the job's lock
            and inflight entries are then cleared. Without it, the missing hash
            a plain delete would leave makes the job look orphaned and it is
            resurrected on every pass, forever.
    """

    job_kind: str
    list_jobs: Callable[[DatabaseAccess], Awaitable[Sequence[Any]]]
    job_id_of: Callable[[Any], str]
    target_queue_key: str
    hash_mapping: Callable[[Any, str, int], Mapping[str, Any]]
    terminalize_exhausted: Callable[[DatabaseAccess, Any], Awaitable[None]] | None = None


def _hash_value(hash_data: Mapping[Any, Any], key: str) -> str | None:
    """Decode a single Valkey hash field from mixed str/bytes test clients."""
    raw = hash_data.get(key)
    if raw is None:
        raw = hash_data.get(key.encode())
    if isinstance(raw, bytes):
        return raw.decode(errors="replace")
    return str(raw) if raw is not None else None


async def _reconcile_one_job(
    spec: _RecoverableKind,
    valkey: Valkey_Client,
    jid: str,
    mapping: Mapping[str, Any],
) -> str:
    """Atomically decide and apply this job's reconciliation outcome.

    Args:
        spec: Per-kind reconciliation spec.
        valkey: Async Valkey client.
        jid: Job id, which is also the queue entry and job-hash suffix.
        mapping: Job-hash mapping to write when the hash must be rebuilt.

    Returns:
        The script outcome: ``tombstoned``, ``inflight``, ``locked``,
        ``queued``, ``hash_repaired``, or ``recovered``.
    """
    return await reconcile_job_state(
        valkey,
        job_id=jid,
        target_queue_key=spec.target_queue_key,
        mapping=mapping,
        now=_time.time(),
    )


async def _read_queue_snapshot(valkey: Valkey_Client) -> _QueueSnapshot:
    """Read queue membership once as a conservative fast-path hint."""

    async def read_ids(key: str) -> set[str]:
        raw_ids = await cast(Any, valkey.lrange(key, 0, -1))
        return {decoded for item in raw_ids if (decoded := decode_valkey_scalar(item)) is not None}

    pending, priority, profiling, inflight = await asyncio.gather(
        read_ids(settings.queue_pending_key),
        read_ids(settings.queue_priority_key),
        read_ids(settings.queue_profiling_key),
        read_ids(settings.queue_inflight_key),
    )
    return _QueueSnapshot(
        queued=pending | priority | profiling,
        inflight=inflight,
    )


async def _rebuild_kind(
    spec: _RecoverableKind,
    db: DatabaseAccess,
    valkey: Valkey_Client,
    snapshot: _QueueSnapshot,
) -> _KindOutcome:
    """Re-enqueue every recoverable job of one kind that is missing from Valkey.

    Jobs that are inflight, locked, or still queued are left untouched — see the
    module docstring for why observed queue membership, and not the database
    status, decides.

    Args:
        spec: Per-kind reconciliation spec.
        db: Open worker database accessor.
        valkey: Async Valkey client.
        snapshot: Conservative initial membership used only to suppress work.

    Returns:
        The per-outcome tally for this kind.
    """
    outcome = _KindOutcome()
    for item in await spec.list_jobs(db):
        jid = spec.job_id_of(item.payload)
        job_key = f"{settings.queue_job_hash_prefix}:{jid}"
        hash_data = await cast(Any, valkey.hgetall(job_key))

        if (
            jid in snapshot.queued
            and jid not in snapshot.inflight
            and hash_data
            and _hash_value(hash_data, "reaper_dropped") != "true"
        ):
            outcome.skipped["queued"] = outcome.skipped.get("queued", 0) + 1
            RECONCILE_SKIPPED_TOTAL.labels(job_kind=spec.job_kind, reason="queued").inc()
            continue

        mapping = spec.hash_mapping(item.payload, jid, hash_requeue_count(hash_data))
        result = await _reconcile_one_job(spec, valkey, jid, mapping)
        if result == "tombstoned":
            if spec.terminalize_exhausted is None:
                logger.error("Unexpected reaper tombstone for job %s of kind %s", jid, spec.job_kind)
                continue
            await spec.terminalize_exhausted(db, item.payload)
            await delete_job_state(valkey, jid)
            outcome.terminalized += 1
            continue

        if result == "recovered":
            snapshot.queued.add(jid)
            snapshot.inflight.discard(jid)
            outcome.recovered += 1
            continue
        outcome.skipped[result] = outcome.skipped.get(result, 0) + 1
        RECONCILE_SKIPPED_TOTAL.labels(job_kind=spec.job_kind, reason=result).inc()
    return outcome


def _submission_spec() -> _RecoverableKind:
    return _RecoverableKind(
        job_kind=JobKind.SUBMISSION,
        list_jobs=lambda db: db.list_recoverable_submission_jobs(),
        job_id_of=lambda payload: payload.judgment_id,
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
        job_kind=JobKind.PROFILING,
        list_jobs=lambda db: db.list_recoverable_profiling_jobs(),
        job_id_of=lambda payload: payload.profiling_run_id,
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


async def _terminalize_exhausted_solution_test(db: DatabaseAccess, payload: Any) -> None:
    """Mark a tombstoned solution-test run FAILED so it stops being resurrected."""
    fail_exhausted = getattr(db, "fail_exhausted_solution_test_run", None)
    if fail_exhausted is not None:
        await fail_exhausted(payload.solution_test_run_id)


def _solution_test_spec() -> _RecoverableKind:
    async def list_jobs(db: DatabaseAccess) -> Sequence[Any]:
        """Support test accessors that predate solution-test recovery."""
        method = getattr(db, "list_recoverable_solution_test_jobs", None)
        return [] if method is None else await method()

    return _RecoverableKind(
        job_kind=JobKind.SOLUTION_TEST,
        list_jobs=list_jobs,
        job_id_of=lambda payload: payload.solution_test_run_id,
        target_queue_key=settings.queue_pending_key,
        hash_mapping=lambda payload, jid, requeue_count: {
            "solution_test_run_id": jid,
            "contest_id": payload.contest_id,
            "problem_id": payload.problem_id,
            "language_id": payload.language_id,
            "requeue_count": str(requeue_count),
            "job_kind": JobKind.SOLUTION_TEST,
        },
        terminalize_exhausted=_terminalize_exhausted_solution_test,
    )


def _arena_submission_spec() -> _RecoverableKind:
    return _RecoverableKind(
        job_kind=JobKind.ARENA_SUBMISSION,
        list_jobs=lambda db: db.list_recoverable_arena_submission_jobs(),
        job_id_of=lambda payload: payload.judgment_id,
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


async def _terminalize_exhausted_custom_validator(db: DatabaseAccess, payload: Any) -> None:
    """Mark a tombstoned validator candidate INVALID so it stops being resurrected."""
    reject_exhausted = getattr(db, "reject_exhausted_custom_validator_validation", None)
    if reject_exhausted is not None:
        await reject_exhausted(
            domain=payload.domain,
            problem_id=payload.problem_id,
            candidate_token=payload.candidate_token,
        )


def _custom_validator_spec() -> _RecoverableKind:
    async def list_jobs(db: DatabaseAccess) -> Sequence[Any]:
        """Support test/rollout accessors that predate validator recovery."""
        method = getattr(db, "list_recoverable_custom_validator_jobs", None)
        return [] if method is None else await method()

    return _RecoverableKind(
        job_kind=JobKind.CUSTOM_VALIDATOR_VALIDATION,
        list_jobs=list_jobs,
        job_id_of=lambda payload: payload.validation_id,
        target_queue_key=settings.queue_profiling_key,
        hash_mapping=lambda payload, jid, requeue_count: {
            "validation_id": jid,
            "domain": payload.domain,
            "problem_id": payload.problem_id,
            "candidate_token": payload.candidate_token,
            "requeue_count": str(requeue_count),
            "job_kind": JobKind.CUSTOM_VALIDATOR_VALIDATION,
        },
        terminalize_exhausted=_terminalize_exhausted_custom_validator,
    )


async def reconcile_queue_state(db: DatabaseAccess, valkey: Valkey_Client, *, phase: str = "Startup") -> None:
    """Rebuild missing queue state from DB so non-terminal jobs are never orphaned.

    Args:
        db: Open worker database accessor.
        valkey: Async Valkey client.
        phase: Label used in the recovery log line ("Startup" or "Periodic").
    """
    snapshot = await _read_queue_snapshot(valkey)
    outcomes = {
        "submissions": await _rebuild_kind(_submission_spec(), db, valkey, snapshot),
        "profiling": await _rebuild_kind(_profiling_spec(), db, valkey, snapshot),
        "solution_tests": await _rebuild_kind(_solution_test_spec(), db, valkey, snapshot),
        "arena_submissions": await _rebuild_kind(_arena_submission_spec(), db, valkey, snapshot),
        "custom_validators": await _rebuild_kind(_custom_validator_spec(), db, valkey, snapshot),
    }

    summary = {
        **{f"recovered_{name}": outcome.recovered for name, outcome in outcomes.items()},
        **{
            f"skipped_{reason}": sum(outcome.skipped.get(reason, 0) for outcome in outcomes.values())
            for reason in _SKIP_OUTCOMES
        },
    }

    if any(outcome.recovered for outcome in outcomes.values()):
        # Skips are the steady state, so only a real recovery is worth a warning;
        # the skip counts ride along as diagnostic context.
        logger.warning("%s reconciliation re-enqueued non-terminal jobs", phase)
        logger.warning(json.dumps(summary, indent=2))
    else:
        logger.debug("%s reconciliation recovered nothing: %s", phase, json.dumps(summary))


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
