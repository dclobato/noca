#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Valkey mail queue the mailer worker drains (issue #155).

Contract: enqueue stores the rendered job as a TTL'd hash and a pending list
entry; dequeue moves the id to inflight and stamps its dispatch time; the
hash round-trips through ``MailJob``; completion clears every key; the stale
scan finds only old inflight ids; the size counts pending plus inflight; and a
``ValkeyRuntime`` wraps the same operations.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from shared.queue_schema import MailJob
from shared.services.email_models import EmailMessage
from shared.services.valkey_service import (
    QUEUE_MAIL_INFLIGHT_KEY,
    QUEUE_MAIL_INFLIGHT_TIMES_KEY,
    QUEUE_MAIL_JOB_HASH_PREFIX,
    QUEUE_MAIL_PENDING_KEY,
    ValkeyRuntime,
    complete_mail_job,
    dequeue_mail_job_id,
    enqueue_mail_job,
    get_mail_job_hash,
    get_stale_mail_job_ids,
    remove_from_mail_inflight,
)
from shared.services.valkey_service.queue_metrics import get_mail_queue_size_with_client

pytestmark = pytest.mark.asyncio


def _job(*, actor_key: str | None = "user:1") -> MailJob:
    message = EmailMessage(
        from_email="noreply@test.example",
        from_name="NOCA",
        to_email=f"{uuid4().hex[:8]}@test.example",
        to_name="Someone",
        subject="Hello",
        text_body="line one\nline two",
    )
    return MailJob.from_message(message, actor_key=actor_key, enqueued_at=time.time())


def _runtime(client: aivalkey.Valkey) -> ValkeyRuntime:
    runtime = ValkeyRuntime(valkey_url="redis://127.0.0.1:6379/15", healthcheck_interval_s=60)
    runtime._client = client  # type: ignore[assignment]
    runtime._is_available = True
    return runtime


async def test_enqueue_stores_a_ttl_hash_and_a_pending_entry(valkey_client: aivalkey.Valkey) -> None:
    job = _job()
    await enqueue_mail_job(valkey_client, job, ttl_seconds=120)

    assert await valkey_client.lrange(QUEUE_MAIL_PENDING_KEY, 0, -1) == [job.job_id]
    assert 0 < await valkey_client.ttl(f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job.job_id}") <= 120
    stored = await get_mail_job_hash(valkey_client, job.job_id)
    assert stored is not None
    assert MailJob.model_validate(stored) == job
    assert MailJob.model_validate(stored).to_message().text_body == "line one\nline two"


async def test_dequeue_moves_to_inflight_and_stamps_the_dispatch_time(valkey_client: aivalkey.Valkey) -> None:
    job = _job()
    await enqueue_mail_job(valkey_client, job, ttl_seconds=120)

    before = time.time()
    assert await dequeue_mail_job_id(valkey_client) == job.job_id
    assert await dequeue_mail_job_id(valkey_client) is None
    assert await valkey_client.lrange(QUEUE_MAIL_PENDING_KEY, 0, -1) == []
    assert await valkey_client.lrange(QUEUE_MAIL_INFLIGHT_KEY, 0, -1) == [job.job_id]
    score = await valkey_client.zscore(QUEUE_MAIL_INFLIGHT_TIMES_KEY, job.job_id)
    assert score is not None and score >= before - 1


async def test_complete_clears_every_key(valkey_client: aivalkey.Valkey) -> None:
    job = _job()
    await enqueue_mail_job(valkey_client, job, ttl_seconds=120)
    await dequeue_mail_job_id(valkey_client)
    await complete_mail_job(valkey_client, job.job_id)

    assert await valkey_client.exists(f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job.job_id}") == 0
    assert await valkey_client.llen(QUEUE_MAIL_INFLIGHT_KEY) == 0
    assert await valkey_client.zcard(QUEUE_MAIL_INFLIGHT_TIMES_KEY) == 0
    assert await get_mail_job_hash(valkey_client, job.job_id) is None


async def test_stale_scan_and_inflight_removal(valkey_client: aivalkey.Valkey) -> None:
    old, fresh = _job(), _job()
    for job in (old, fresh):
        await enqueue_mail_job(valkey_client, job, ttl_seconds=120)
        await dequeue_mail_job_id(valkey_client)
    await valkey_client.zadd(QUEUE_MAIL_INFLIGHT_TIMES_KEY, {old.job_id: time.time() - 1000})

    assert await get_stale_mail_job_ids(valkey_client, 300) == [old.job_id]
    await remove_from_mail_inflight(valkey_client, old.job_id)
    assert await get_stale_mail_job_ids(valkey_client, 300) == []
    assert await valkey_client.lrange(QUEUE_MAIL_INFLIGHT_KEY, 0, -1) == [fresh.job_id]


async def test_queue_size_counts_pending_and_inflight(valkey_client: aivalkey.Valkey) -> None:
    for _ in range(3):
        await enqueue_mail_job(valkey_client, _job(), ttl_seconds=120)
    await dequeue_mail_job_id(valkey_client)

    assert await get_mail_queue_size_with_client(valkey_client) == 3


async def test_runtime_wraps_the_same_operations(valkey_client: aivalkey.Valkey) -> None:
    runtime = _runtime(valkey_client)
    job = _job(actor_key=None)
    await runtime.enqueue_mail_job(job, ttl_seconds=90)

    assert await runtime.get_mail_queue_size() == 1
    assert await runtime.dequeue_mail_job_id() == job.job_id
    stored = await runtime.get_mail_job_hash(job.job_id)
    assert stored is not None and "actor_key" not in stored
    assert MailJob.model_validate(stored).actor_key is None
    await runtime.complete_mail_job(job.job_id)
    assert await runtime.get_mail_queue_size() == 0
