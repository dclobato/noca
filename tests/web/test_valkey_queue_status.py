from __future__ import annotations

from uuid import uuid4

from scripts.web.valkey_queue_status import collect_queue_metrics
from shared.services.valkey_service import QUEUE_UNKNOWN_CONTEST as UNKNOWN_CONTEST_BUCKET
from web.config import settings


async def test_collect_queue_metrics_groups_by_queue_and_contest(valkey_client) -> None:
    contest_a = str(uuid4())
    contest_b = str(uuid4())

    priority_id = str(uuid4())
    pending_id = str(uuid4())
    inflight_legacy_id = str(uuid4())
    pending_missing_hash_id = str(uuid4())

    await valkey_client.lpush(settings.queue_priority_key, priority_id)
    await valkey_client.lpush(settings.queue_pending_key, pending_id)
    await valkey_client.lpush(settings.queue_pending_key, pending_missing_hash_id)
    await valkey_client.lpush(settings.queue_inflight_key, inflight_legacy_id)

    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{priority_id}",
        mapping={
            "judgment_id": priority_id,
            "contest_id": contest_a,
            "is_rejudge": "false",
            "requeue_count": "0",
        },
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{pending_id}",
        mapping={
            "judgment_id": pending_id,
            "contest_id": contest_b,
            "is_rejudge": "false",
            "requeue_count": "0",
        },
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{inflight_legacy_id}",
        mapping={
            "judgment_id": inflight_legacy_id,
            "is_rejudge": "true",
            "requeue_count": "1",
        },
    )

    metrics = await collect_queue_metrics(valkey_client)

    by_queue = metrics["by_queue_and_contest"]
    assert by_queue[settings.queue_priority_key] == {contest_a: 1}
    assert by_queue[settings.queue_pending_key] == {UNKNOWN_CONTEST_BUCKET: 1, contest_b: 1}
    assert by_queue[settings.queue_inflight_key] == {UNKNOWN_CONTEST_BUCKET: 1}

    totals = metrics["by_contest_total"]
    assert totals[contest_a] == 1
    assert totals[contest_b] == 1
    assert totals[UNKNOWN_CONTEST_BUCKET] == 2

    assert metrics["total_items"] == 4
    assert metrics["unknown_contest_items"] == 2
    assert metrics["missing_ratio"] == 0.5
    assert metrics["metrics_latency_ms"] >= 0.0
