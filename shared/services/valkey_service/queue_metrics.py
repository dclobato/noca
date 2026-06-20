#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Queue metrics helpers backed by Valkey."""

from __future__ import annotations

import time
from typing import Any, cast

import valkey.asyncio as aivalkey

from shared.queue_schema import AllContestQueueMetrics, ContestQueueMetrics
from shared.services.valkey_service.constants import (
    QUEUE_JOB_HASH_PREFIX,
    QUEUE_KEYS,
    QUEUE_PENDING_KEY,
    QUEUE_PRIORITY_KEY,
    QUEUE_PROFILING_KEY,
    QUEUE_UNKNOWN_CONTEST,
)


def contest_id_from_job_hash(job_hash: dict[str, str] | None) -> str | None:
    """Extract a contest id from a stored Valkey job hash."""
    if not job_hash:
        return None
    contest_id = job_hash.get("contest_id", "").strip()
    return contest_id or None


async def read_queue_ids_with_client(client: aivalkey.Valkey) -> dict[str, list[str]]:
    """Read every job id currently present in each queue."""
    queue_ids: dict[str, list[str]] = {}
    for queue_key in QUEUE_KEYS:
        queue_ids[queue_key] = cast(list[str], await cast(Any, client.lrange(queue_key, 0, -1)))
    return queue_ids


async def read_job_hashes_for_ids_with_client(
    client: aivalkey.Valkey,
    judgment_ids: list[str],
) -> dict[str, dict[str, str]]:
    """Fetch stored job hashes for all *judgment_ids*."""
    if not judgment_ids:
        return {}

    pipe = client.pipeline()
    for judgment_id in judgment_ids:
        pipe.hgetall(f"{QUEUE_JOB_HASH_PREFIX}:{judgment_id}")
    raw_hashes = await pipe.execute()

    parsed: dict[str, dict[str, str]] = {}
    for judgment_id, job_hash in zip(judgment_ids, raw_hashes, strict=True):
        parsed[judgment_id] = dict(job_hash or {})
    return parsed


async def get_contest_queue_metrics_with_client(
    client: aivalkey.Valkey,
    contest_id: str,
) -> ContestQueueMetrics:
    """Return queue metrics for a single contest."""
    started = time.perf_counter()
    queue_ids = await read_queue_ids_with_client(client)
    unique_ids: list[str] = sorted({job_id for ids in queue_ids.values() for job_id in ids})
    job_hashes = await read_job_hashes_for_ids_with_client(client, unique_ids)

    profiling_count = 0
    priority_count = 0
    pending_count = 0
    inflight_count = 0
    unknown_contest_seen = 0

    for queue_key, ids in queue_ids.items():
        queue_count = 0
        for judgment_id in ids:
            job_contest_id = contest_id_from_job_hash(job_hashes.get(judgment_id))
            if job_contest_id is None:
                unknown_contest_seen += 1
                continue
            if job_contest_id == contest_id:
                queue_count += 1

        if queue_key == QUEUE_PROFILING_KEY:
            profiling_count = queue_count
            continue
        if queue_key == QUEUE_PRIORITY_KEY:
            priority_count = queue_count
            continue
        if queue_key == QUEUE_PENDING_KEY:
            pending_count = queue_count
            continue
        inflight_count = queue_count

    total_count = profiling_count + priority_count + pending_count + inflight_count
    scanned_queue_items = sum(len(ids) for ids in queue_ids.values())
    metrics_latency_ms = (time.perf_counter() - started) * 1000.0
    return ContestQueueMetrics(
        contest_id=contest_id,
        profiling_count=profiling_count,
        priority_count=priority_count,
        pending_count=pending_count,
        inflight_count=inflight_count,
        total_count=total_count,
        unknown_contest_seen=unknown_contest_seen,
        scanned_queue_items=scanned_queue_items,
        metrics_latency_ms=metrics_latency_ms,
    )


async def get_all_contest_queue_metrics_with_client(client: aivalkey.Valkey) -> AllContestQueueMetrics:
    """Aggregate queue counts across all contests from Valkey only."""
    started = time.perf_counter()
    queue_ids = await read_queue_ids_with_client(client)
    unique_ids: list[str] = sorted({job_id for ids in queue_ids.values() for job_id in ids})
    job_hashes = await read_job_hashes_for_ids_with_client(client, unique_ids)

    by_queue_and_contest: dict[str, dict[str, int]] = {}
    by_contest_total: dict[str, int] = {}
    total_items = 0
    unknown_contest_items = 0

    for queue_key, ids in queue_ids.items():
        queue_counts: dict[str, int] = {}
        for judgment_id in ids:
            bucket = contest_id_from_job_hash(job_hashes.get(judgment_id)) or QUEUE_UNKNOWN_CONTEST
            queue_counts[bucket] = queue_counts.get(bucket, 0) + 1
            by_contest_total[bucket] = by_contest_total.get(bucket, 0) + 1
            total_items += 1
            if bucket == QUEUE_UNKNOWN_CONTEST:
                unknown_contest_items += 1
        by_queue_and_contest[queue_key] = dict(sorted(queue_counts.items()))

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    missing_ratio = (unknown_contest_items / total_items) if total_items else 0.0

    return AllContestQueueMetrics(
        by_queue_and_contest=by_queue_and_contest,
        by_contest_total=dict(sorted(by_contest_total.items())),
        total_items=total_items,
        unknown_contest_items=unknown_contest_items,
        missing_ratio=missing_ratio,
        metrics_latency_ms=elapsed_ms,
    )


async def get_autojudge_arena_queue_size_with_client(client: aivalkey.Valkey) -> int:
    """Count pending+inflight autojudge jobs that originated from Arena.

    Only jobs with ``job_kind == "arena_submission"`` are counted; web
    submissions, profiling runs, and hash-less stale entries are excluded.
    """
    from shared.queue_schema import JobKind
    from shared.services.valkey_service.constants import (
        QUEUE_INFLIGHT_KEY,
        QUEUE_JOB_HASH_PREFIX,
        QUEUE_PENDING_KEY,
        QUEUE_PRIORITY_KEY,
    )

    judge_keys = (QUEUE_PRIORITY_KEY, QUEUE_PENDING_KEY, QUEUE_INFLIGHT_KEY)
    all_ids: list[str] = []
    for key in judge_keys:
        all_ids.extend(cast(list[str], await cast(Any, client.lrange(key, 0, -1))))

    unique_ids = list(dict.fromkeys(all_ids))
    if not unique_ids:
        return 0

    pipe = client.pipeline()
    for job_id in unique_ids:
        pipe.hget(f"{QUEUE_JOB_HASH_PREFIX}:{job_id}", "job_kind")
    raw_kinds: list[str | None] = await pipe.execute()

    return sum(1 for kind in raw_kinds if kind == JobKind.ARENA_SUBMISSION)


async def get_ai_review_queue_size_with_client(client: aivalkey.Valkey) -> int:
    """Return the total number of AI review jobs in pending + inflight queues."""
    from shared.services.valkey_service.constants import (
        QUEUE_AI_REVIEW_INFLIGHT_KEY,
        QUEUE_AI_REVIEW_PENDING_KEY,
    )

    pipe = client.pipeline()
    pipe.llen(QUEUE_AI_REVIEW_PENDING_KEY)
    pipe.llen(QUEUE_AI_REVIEW_INFLIGHT_KEY)
    pending, inflight = await pipe.execute()
    return int(pending) + int(inflight)
