#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``EmailService``: the mailer is the only sender (issue #155).

Contract: a producer with a Valkey runtime never sends -- every accepted
message becomes a ``MailJob`` handed to the runtime with the configured TTL,
answered as a ``queue`` result, and a queue that cannot take the job is a
failure, never a silent buffer; the mailer alone builds the SMTP provider and
validates its settings; without a runtime the service keeps an in-process mock
outbox, a test double that runs off the event loop; and a spent budget raises
before anything is queued.
"""

from __future__ import annotations

import logging
import threading

import pytest

from shared.queue_schema import MailJob
from shared.services.email_budget import EmailBudgetPolicy
from shared.services.email_models import EmailMessage
from shared.services.email_providers import EmailBudgetExceeded, EmailProviderError, MockProvider, QueueProvider
from shared.services.email_service import QUEUE_PROVIDER_NAME, EmailConfig, EmailService


class _Runtime:
    """Captures enqueued jobs and answers the budget script from an in-memory counter."""

    def __init__(self, *, down: bool = False) -> None:
        self.jobs: list[tuple[MailJob, int]] = []
        self.counts: dict[str, int] = {}
        self.down = down

    async def enqueue_mail_job(self, job: MailJob, *, ttl_seconds: int) -> None:
        if self.down:
            raise RuntimeError("Valkey is unavailable; the mail job was not queued")
        self.jobs.append((job, ttl_seconds))

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        key = args[0]
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], 600_000]


def _config(**overrides: object) -> EmailConfig:
    base: dict[str, object] = dict(default_from_email="noreply@test.example", default_from_name="NOCA")
    base.update(overrides)
    return EmailConfig(**base)  # type: ignore[arg-type]


def test_config_is_validated() -> None:
    with pytest.raises(ValueError, match="NOCA_EMAIL_PROVIDER"):
        _config(provider_type="carrier-pigeon")
    with pytest.raises(ValueError, match="TTL"):
        _config(queue_job_ttl_seconds=0)
    with pytest.raises(ValueError, match="NOCA_EMAIL_SENDER"):
        _config(default_from_email=" ")


def test_only_the_worker_builds_smtp_and_validates_its_settings() -> None:
    assert _config().worker_delivery_mode == "mock"
    assert _config(send_email=True, provider_type="mock").worker_delivery_mode == "mock"
    assert _config(send_email=True).worker_delivery_mode == "smtp"
    assert isinstance(_config().create_worker_provider(), MockProvider)
    with pytest.raises(ValueError, match="Missing required SMTP settings"):
        _config(send_email=True).create_worker_provider()
    provider = _config(
        send_email=True, smtp_server="smtp.test.example", smtp_username="u", smtp_password="p"
    ).create_worker_provider()
    assert provider.get_provider_name() == "SMTP (smtp.test.example)"


def test_a_producer_holds_the_queue_placeholder_whatever_its_config_says() -> None:
    service = EmailService(
        config=_config(send_email=True, smtp_server="smtp", smtp_username="u", smtp_password="p"),
        logger=logging.getLogger("t"),
        valkey_runtime=_Runtime(),
    )
    assert isinstance(service.provider, QueueProvider)
    assert service.get_provider_info()["provider_name"] == "Queue (noca-mailer)"
    with pytest.raises(EmailProviderError, match="mailer worker delivers"):
        service.provider.send(EmailMessage(to_email="a@test.example", text_body="b"))


@pytest.mark.asyncio
async def test_without_a_runtime_the_mock_outbox_records_off_the_event_loop(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="t"):
        service = EmailService(config=_config(), logger=logging.getLogger("t"))
    assert "tests only" in caplog.text
    provider = service.provider
    assert isinstance(provider, MockProvider)
    seen_threads: list[str] = []
    original = provider.send

    def _send(message):  # type: ignore[no-untyped-def]
        seen_threads.append(threading.current_thread().name)
        return original(message)

    provider.send = _send  # type: ignore[method-assign]

    result = await service.send_email(to_email="a@test.example", subject="Hi", text_body="Body")

    assert result.success and result.provider == "mock"
    assert [m["to"] for m in provider.get_sent_emails()] == ["a@test.example"]
    assert seen_threads and seen_threads[0] != threading.main_thread().name
    assert service.get_provider_info()["delivery_mode"] == "mock"


@pytest.mark.asyncio
async def test_with_a_runtime_every_message_is_a_job_for_the_mailer() -> None:
    runtime = _Runtime()
    service = EmailService(
        config=_config(queue_job_ttl_seconds=900), logger=logging.getLogger("t"), valkey_runtime=runtime
    )

    result = await service.send_email(
        to_email="a@test.example", to_name="A", subject="Hi", text_body="Body", actor_key="user:1"
    )

    assert result.success and result.provider == QUEUE_PROVIDER_NAME
    assert len(runtime.jobs) == 1
    job, ttl = runtime.jobs[0]
    assert ttl == 900 and result.message_id == job.job_id
    assert job.to_email == "a@test.example" and job.from_email == "noreply@test.example"
    assert job.from_name == "NOCA" and job.subject == "Hi" and job.text_body == "Body"
    assert job.actor_key == "user:1" and job.enqueued_at > 0
    assert service.get_provider_info()["delivery_mode"] == "queue"


@pytest.mark.asyncio
async def test_a_queue_that_cannot_take_the_job_is_a_failure_not_a_buffer() -> None:
    runtime = _Runtime(down=True)
    service = EmailService(config=_config(), logger=logging.getLogger("t"), valkey_runtime=runtime)

    with pytest.raises(EmailProviderError, match="Mail queue unavailable"):
        await service.send_email(to_email="a@test.example", subject="Hi", text_body="Body")
    assert runtime.jobs == []


@pytest.mark.asyncio
async def test_budget_refuses_before_anything_is_queued() -> None:
    runtime = _Runtime()
    policy = EmailBudgetPolicy(enabled=True, window_seconds=600, user_max=1, admin_max=2)
    service = EmailService(config=_config(budget=policy), logger=logging.getLogger("t"), valkey_runtime=runtime)

    await service.send_email(to_email="a@test.example", subject="1", text_body="b", actor_key="user:x")
    with pytest.raises(EmailBudgetExceeded) as excinfo:
        await service.send_email(to_email="a@test.example", subject="2", text_body="b", actor_key="user:x")
    assert excinfo.value.retry_after_seconds == 600 and excinfo.value.actor_key == "user:x"
    assert len(runtime.jobs) == 1

    # The admin tier has its own ceiling.
    await service.send_email(to_email="a@test.example", subject="3", text_body="b", actor_key="user:x", tier="admin")
    await service.send_email(to_email="a@test.example", subject="4", text_body="b", actor_key="user:x", tier="admin")
    with pytest.raises(EmailBudgetExceeded):
        await service.send_email(
            to_email="a@test.example", subject="5", text_body="b", actor_key="user:x", tier="admin"
        )
    assert len(runtime.jobs) == 3

    # System-originated email (no actor) is never budgeted.
    for _ in range(3):
        await service.send_email(to_email="a@test.example", subject="sys", text_body="b")
    assert len(runtime.jobs) == 6
