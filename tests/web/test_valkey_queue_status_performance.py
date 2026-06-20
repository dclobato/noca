from __future__ import annotations

import time
from uuid import uuid4

import pytest

from scripts.web.valkey_queue_status import collect_queue_metrics
from web.config import settings


@pytest.mark.slow
async def test_collect_queue_metrics_runtime_budget_10k(valkey_client) -> None:
    total_jobs = 10_000
    contest_ids = [str(uuid4()) for _ in range(10)]
    queue_keys = [
        settings.queue_priority_key,
        settings.queue_pending_key,
        settings.queue_inflight_key,
    ]

    pipe = valkey_client.pipeline()
    for idx in range(total_jobs):
        judgment_id = str(uuid4())
        contest_id = contest_ids[idx % len(contest_ids)]
        queue_key = queue_keys[idx % len(queue_keys)]
        job_key = f"{settings.queue_job_hash_prefix}:{judgment_id}"

        pipe.lpush(queue_key, judgment_id)
        pipe.hset(
            job_key,
            mapping={
                "judgment_id": judgment_id,
                "contest_id": contest_id,
                "is_rejudge": "false",
                "requeue_count": "0",
            },
        )

        if (idx + 1) % 1000 == 0:
            await pipe.execute()
            pipe = valkey_client.pipeline()

    await pipe.execute()

    started = time.perf_counter()
    metrics = await collect_queue_metrics(valkey_client)
    elapsed_s = time.perf_counter() - started

    assert metrics["total_items"] == total_jobs
    assert elapsed_s < 8.0
