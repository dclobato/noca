#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The mailer's delivery step and reaper against a real Valkey (issue #155).

Contract: a dequeued job is delivered as rendered and its queue state cleared;
a job past its TTL is dropped without touching the provider; a provider
refusal leaves the job inflight for the reaper, which requeues it with an
incremented count and drops it past the cap; a missing payload is discarded.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from mailer.reaper import handle_stale_job
from mailer.worker import process_job
from shared.queue_schema import MailJob
from shared.services.email_models import EmailMessage, EmailResult
from shared.services.email_providers import EmailProviderError, MockProvider
from shared.services.valkey_service import (
    QUEUE_MAIL_INFLIGHT_KEY,
    QUEUE_MAIL_INFLIGHT_TIMES_KEY,
    QUEUE_MAIL_JOB_HASH_PREFIX,
    QUEUE_MAIL_PENDING_KEY,
    ValkeyRuntime,
    dequeue_mail_job_id,
    enqueue_mail_job,
)

pytestmark = pytest.mark.asyncio


class _RefusingProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__(log_emails=False)
        self.attempts = 0

    def send(self, message: EmailMessage) -> EmailResult:
        self.attempts += 1
        raise EmailProviderError("SMTP refused")


def _runtime(client: aivalkey.Valkey) -> ValkeyRuntime:
    runtime = ValkeyRuntime(valkey_url="redis://127.0.0.1:6379/15", healthcheck_interval_s=60)
    runtime._client = client  # type: ignore[assignment]
    runtime._is_available = True
    return runtime


async def _queued(client: aivalkey.Valkey, *, enqueued_at: float | None = None) -> MailJob:
    message = EmailMessage(
        from_email="noreply@test.example",
        from_name="NOCA",
        to_email=f"{uuid4().hex[:8]}@test.example",
        to_name="Someone",
        subject="Your credentials",
        text_body="secret",
    )
    job = MailJob.from_message(message, actor_key="user:1", enqueued_at=enqueued_at or time.time())
    await enqueue_mail_job(client, job, ttl_seconds=3600)
    dequeued = await dequeue_mail_job_id(client)
    assert dequeued == job.job_id
    return job


async def _state(client: aivalkey.Valkey, job_id: str) -> tuple[int, list[str], bool]:
    return (
        await client.llen(QUEUE_MAIL_PENDING_KEY),
        await client.lrange(QUEUE_MAIL_INFLIGHT_KEY, 0, -1),
        bool(await client.exists(f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job_id}")),
    )


async def test_delivers_the_rendered_job_and_clears_it(valkey_client: aivalkey.Valkey) -> None:
    runtime = _runtime(valkey_client)
    provider = MockProvider(log_emails=False)
    job = await _queued(valkey_client)

    attempted = await process_job(job.job_id, runtime, provider, job_ttl_seconds=3600)

    assert attempted is True
    sent = provider.get_sent_emails()
    assert len(sent) == 1 and sent[0]["subject"] == "Your credentials" and sent[0]["text_body"] == "secret"
    assert job.to_email in sent[0]["to"]
    assert await _state(valkey_client, job.job_id) == (0, [], False)


async def test_a_job_past_its_ttl_is_dropped_unsent(valkey_client: aivalkey.Valkey) -> None:
    runtime = _runtime(valkey_client)
    provider = MockProvider(log_emails=False)
    job = await _queued(valkey_client, enqueued_at=time.time() - 7200)

    attempted = await process_job(job.job_id, runtime, provider, job_ttl_seconds=3600)

    assert attempted is False
    assert provider.get_sent_emails() == []
    assert await _state(valkey_client, job.job_id) == (0, [], False)


async def test_a_missing_payload_is_discarded(valkey_client: aivalkey.Valkey) -> None:
    runtime = _runtime(valkey_client)
    provider = MockProvider(log_emails=False)
    job = await _queued(valkey_client)
    await valkey_client.delete(f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job.job_id}")

    attempted = await process_job(job.job_id, runtime, provider, job_ttl_seconds=3600)

    assert attempted is False
    assert provider.get_sent_emails() == []
    assert await _state(valkey_client, job.job_id) == (0, [], False)


async def test_a_refused_job_stays_inflight_and_the_reaper_requeues_then_drops_it(
    valkey_client: aivalkey.Valkey,
) -> None:
    runtime = _runtime(valkey_client)
    provider = _RefusingProvider()
    job = await _queued(valkey_client)
    logger = logging.getLogger("test-mailer")

    attempted = await process_job(job.job_id, runtime, provider, job_ttl_seconds=3600)
    assert attempted is True and provider.attempts == 1
    assert await _state(valkey_client, job.job_id) == (0, [job.job_id], True)

    # The reaper requeues it atomically with an incremented count, and the hash
    # keeps its original TTL: a retry never extends the credential-at-rest bound.
    assert await handle_stale_job(runtime, job.job_id, max_requeue_count=1, logger=logger) == "requeued"
    assert await _state(valkey_client, job.job_id) == (1, [], True)
    assert await valkey_client.hget(f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job.job_id}", "requeue_count") == "1"
    assert 3000 < await valkey_client.ttl(f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job.job_id}") <= 3600
    assert await valkey_client.zcard(QUEUE_MAIL_INFLIGHT_TIMES_KEY) == 0

    # ...and past the cap it is dropped as a poison pill.
    assert await dequeue_mail_job_id(valkey_client) == job.job_id
    assert await handle_stale_job(runtime, job.job_id, max_requeue_count=1, logger=logger) == "dropped"
    assert await _state(valkey_client, job.job_id) == (0, [], False)

    # A job the worker completed meanwhile is left alone, and an expired one only
    # loses its inflight entry.
    assert await handle_stale_job(runtime, job.job_id, max_requeue_count=1, logger=logger) == "not_inflight"
    other = await _queued(valkey_client)
    await valkey_client.delete(f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{other.job_id}")
    assert await handle_stale_job(runtime, other.job_id, max_requeue_count=1, logger=logger) == "expired"
    assert await _state(valkey_client, other.job_id) == (0, [], False)
