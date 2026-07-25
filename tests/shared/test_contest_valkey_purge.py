#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for strict contest-scoped Valkey cleanup."""

from __future__ import annotations

import pytest

from shared.queue_schema import JudgeJob
from shared.services.scoreboard_cache import (
    scoreboard_final_key,
    scoreboard_frozen_key,
    scoreboard_full_key,
    scoreboard_public_key,
)
from shared.services.valkey_service import (
    QUEUE_INFLIGHT_KEY,
    QUEUE_INFLIGHT_TIMES_KEY,
    QUEUE_JOB_HASH_PREFIX,
    QUEUE_PENDING_KEY,
    QUEUE_PRIORITY_KEY,
    QUEUE_PROFILING_KEY,
    ContestValkeyTargets,
    PendingCommand,
    ValkeyRuntime,
    contest_runtime_keys,
    purge_contest_with_client,
)
from web.config import settings


@pytest.mark.asyncio
async def test_purge_removes_only_target_contest_runtime_state(valkey_client) -> None:
    targets = ContestValkeyTargets(
        contest_id="contest-target",
        judgment_ids=frozenset({"judgment-target"}),
        profiling_run_ids=frozenset({"profiling-target"}),
        validation_ids=frozenset({"validation-target"}),
        task_ids=frozenset({"task-target"}),
        clarification_ids=frozenset({"clarification-target"}),
    )
    target_ids = sorted(targets.job_ids)
    orphan_queued_id = "orphan-target-queued"
    orphan_hash_id = "orphan-target-hash"
    unrelated_id = "judgment-unrelated"

    for queue_key in (
        QUEUE_PENDING_KEY,
        QUEUE_PRIORITY_KEY,
        QUEUE_PROFILING_KEY,
        QUEUE_INFLIGHT_KEY,
    ):
        await valkey_client.rpush(
            queue_key,
            *target_ids,
            target_ids[0],
            orphan_queued_id,
            unrelated_id,
        )
    await valkey_client.zadd(
        QUEUE_INFLIGHT_TIMES_KEY,
        {
            **{job_id: 1 for job_id in target_ids},
            orphan_queued_id: 1,
            unrelated_id: 2,
        },
    )
    for job_id in (*target_ids, orphan_queued_id, orphan_hash_id, unrelated_id):
        await valkey_client.hset(
            f"{QUEUE_JOB_HASH_PREFIX}:{job_id}",
            mapping={"contest_id": "contest-unrelated" if job_id == unrelated_id else "contest-target"},
        )
        await valkey_client.set(f"judge:lock:{job_id}", "worker")
    for key in contest_runtime_keys(targets):
        if not await valkey_client.exists(key):
            await valkey_client.set(key, "target")
    orphan_workflow_lock = "lock:future:contest-target:orphan"
    unrelated_workflow_lock = "lock:future:contest-unrelated:orphan"
    await valkey_client.set(orphan_workflow_lock, "target")
    await valkey_client.set(unrelated_workflow_lock, "unrelated")

    unrelated_scoreboards = (
        scoreboard_full_key("contest-unrelated"),
        scoreboard_public_key("contest-unrelated"),
        scoreboard_frozen_key("contest-unrelated"),
        scoreboard_final_key("contest-unrelated"),
    )
    for key in unrelated_scoreboards:
        await valkey_client.set(key, "unrelated")

    result = await purge_contest_with_client(valkey_client, targets)

    assert result.queue_entries_removed == (len(target_ids) + 2) * 4
    for queue_key in (
        QUEUE_PENDING_KEY,
        QUEUE_PRIORITY_KEY,
        QUEUE_PROFILING_KEY,
        QUEUE_INFLIGHT_KEY,
    ):
        assert await valkey_client.lrange(queue_key, 0, -1) == [unrelated_id]
    assert await valkey_client.zscore(QUEUE_INFLIGHT_TIMES_KEY, unrelated_id) == 2
    assert await valkey_client.exists(*contest_runtime_keys(targets)) == 0
    assert await valkey_client.exists(orphan_workflow_lock) == 0
    assert await valkey_client.get(unrelated_workflow_lock) == "unrelated"
    assert await valkey_client.hgetall(f"{QUEUE_JOB_HASH_PREFIX}:{unrelated_id}")
    assert await valkey_client.get(f"judge:lock:{unrelated_id}") == "worker"
    assert await valkey_client.exists(*unrelated_scoreboards) == len(unrelated_scoreboards)


@pytest.mark.asyncio
async def test_runtime_purge_discards_only_target_buffered_commands(valkey_client) -> None:
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()
    try:
        target_job = JudgeJob(judgment_id="target-job", contest_id="target-contest")
        unrelated_job = JudgeJob(judgment_id="other-job", contest_id="other-contest")
        runtime._pending_commands.extend(  # noqa: SLF001 - verifies the runtime buffer contract
            (
                PendingCommand(operation="enqueue_job", job=target_job),
                PendingCommand(operation="enqueue_job", job=unrelated_job),
            )
        )

        result = await runtime.purge_contest_runtime_state(
            ContestValkeyTargets(
                contest_id="target-contest",
                judgment_ids=frozenset({"target-job"}),
            )
        )

        assert result.buffered_commands_removed == 1
        assert runtime.pending_count == 1
        assert runtime._pending_commands[0].job == unrelated_job  # noqa: SLF001
    finally:
        await runtime.stop()
