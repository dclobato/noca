#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Strict removal of one contest's ephemeral Valkey state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import valkey.asyncio as aivalkey

from shared.services.contest_report_cache import contest_report_generation_key
from shared.services.scoreboard_cache import (
    scoreboard_final_key,
    scoreboard_frozen_key,
    scoreboard_full_key,
    scoreboard_public_key,
)
from shared.services.valkey_service.constants import (
    QUEUE_INFLIGHT_TIMES_KEY,
    QUEUE_JOB_HASH_PREFIX,
    QUEUE_KEYS,
)


class ContestValkeyPurgeError(RuntimeError):
    """Raised when contest runtime state cannot be removed and verified."""


@dataclass(frozen=True, slots=True)
class ContestValkeyTargets:
    """Identifiers whose runtime state belongs to one contest."""

    contest_id: str
    judgment_ids: frozenset[str] = frozenset()
    profiling_run_ids: frozenset[str] = frozenset()
    solution_test_run_ids: frozenset[str] = frozenset()
    validation_ids: frozenset[str] = frozenset()
    task_ids: frozenset[str] = frozenset()
    clarification_ids: frozenset[str] = frozenset()

    @property
    def job_ids(self) -> frozenset[str]:
        """Return all queue and autojudge-lock identifiers."""
        return self.judgment_ids | self.profiling_run_ids | self.solution_test_run_ids | self.validation_ids


@dataclass(frozen=True, slots=True)
class ContestValkeyPurgeResult:
    """Verified Valkey removal counts for one contest."""

    queue_entries_removed: int
    keys_removed: int
    buffered_commands_removed: int = 0


def contest_runtime_keys(targets: ContestValkeyTargets) -> tuple[str, ...]:
    """Build every direct Valkey key owned by the target contest."""
    keys = {
        contest_report_generation_key(targets.contest_id),
        scoreboard_full_key(targets.contest_id),
        scoreboard_public_key(targets.contest_id),
        scoreboard_frozen_key(targets.contest_id),
        scoreboard_final_key(targets.contest_id),
    }
    keys.update(f"{QUEUE_JOB_HASH_PREFIX}:{job_id}" for job_id in targets.job_ids)
    keys.update(f"judge:lock:{job_id}" for job_id in targets.job_ids)
    keys.update(f"lock:review:{targets.contest_id}:{judgment_id}" for judgment_id in targets.judgment_ids)
    keys.update(f"lock:task:{targets.contest_id}:{task_id}" for task_id in targets.task_ids)
    keys.update(
        f"lock:clarification:{targets.contest_id}:{clarification_id}" for clarification_id in targets.clarification_ids
    )
    return tuple(sorted(keys))


async def purge_contest_with_client(
    client: aivalkey.Valkey,
    targets: ContestValkeyTargets,
) -> ContestValkeyPurgeResult:
    """Atomically remove target state, then strictly verify it is absent.

    Args:
        client: Connected raw async Valkey client.
        targets: Contest-scoped identifiers to remove.

    Returns:
        Verified removal counts.

    Raises:
        ContestValkeyPurgeError: If Valkey is unavailable or verification fails.
    """
    try:
        await client.ping()
        discovered_job_ids: set[str] = set()
        discovered_lock_keys: set[str] = set()
        async for raw_key in client.scan_iter(match=f"{QUEUE_JOB_HASH_PREFIX}:*"):
            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
            raw_contest_id = client.hget(key, "contest_id")
            if asyncio.iscoroutine(raw_contest_id):
                raw_contest_id = await raw_contest_id
            stored_contest_id = raw_contest_id.decode() if isinstance(raw_contest_id, bytes) else raw_contest_id
            if stored_contest_id == targets.contest_id:
                discovered_job_ids.add(key.removeprefix(f"{QUEUE_JOB_HASH_PREFIX}:"))
        async for raw_key in client.scan_iter(match=f"lock:*:{targets.contest_id}:*"):
            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
            discovered_lock_keys.add(key)
        effective_targets = replace(
            targets,
            judgment_ids=targets.judgment_ids | frozenset(discovered_job_ids),
        )
        job_ids = sorted(effective_targets.job_ids)
        direct_keys = tuple(sorted(set(contest_runtime_keys(effective_targets)) | discovered_lock_keys))

        pipe = client.pipeline(transaction=True)
        for queue_key in QUEUE_KEYS:
            for job_id in job_ids:
                pipe.lrem(queue_key, 0, job_id)
        if job_ids:
            pipe.zrem(QUEUE_INFLIGHT_TIMES_KEY, *job_ids)
        if direct_keys:
            pipe.delete(*direct_keys)
        raw_results = await pipe.execute()

        verification = client.pipeline(transaction=False)
        for queue_key in QUEUE_KEYS:
            for job_id in job_ids:
                verification.lpos(queue_key, job_id)
        if job_ids:
            verification.zmscore(QUEUE_INFLIGHT_TIMES_KEY, job_ids)
        if direct_keys:
            verification.exists(*direct_keys)
        verification_results = await verification.execute()
    except Exception as exc:
        raise ContestValkeyPurgeError("Valkey contest cleanup failed.") from exc

    queue_probe_count = len(QUEUE_KEYS) * len(job_ids)
    if any(result is not None for result in verification_results[:queue_probe_count]):
        raise ContestValkeyPurgeError("Valkey contest cleanup verification found queued jobs.")

    verification_index = queue_probe_count
    if job_ids:
        scores = verification_results[verification_index]
        verification_index += 1
        if not isinstance(scores, list) or any(score is not None for score in scores):
            raise ContestValkeyPurgeError("Valkey contest cleanup verification found inflight timestamps.")
    if direct_keys and verification_results[verification_index] != 0:
        raise ContestValkeyPurgeError("Valkey contest cleanup verification found residual keys.")

    queue_entries_removed = sum(int(value or 0) for value in raw_results[:queue_probe_count])
    keys_removed = int(raw_results[-1] or 0) if direct_keys else 0
    return ContestValkeyPurgeResult(
        queue_entries_removed=queue_entries_removed,
        keys_removed=keys_removed,
    )


def pending_command_belongs_to_contest(command: Any, targets: ContestValkeyTargets) -> bool:
    """Return whether a buffered runtime command references target data."""
    job = getattr(command, "job", None)
    if job is not None:
        if getattr(job, "contest_id", None) == targets.contest_id:
            return True
        job_identifiers = {
            getattr(job, "judgment_id", None),
            getattr(job, "profiling_run_id", None),
            getattr(job, "solution_test_run_id", None),
            getattr(job, "validation_id", None),
        }
        if any(identifier in targets.job_ids for identifier in job_identifiers if identifier):
            return True

    job_id = getattr(command, "job_id", None)
    if job_id in targets.job_ids:
        return True

    event = getattr(command, "event", None)
    return bool(
        event is not None
        and (
            getattr(event, "contest_id", None) == targets.contest_id
            or getattr(event, "judgment_id", None) in targets.judgment_ids
        )
    )
