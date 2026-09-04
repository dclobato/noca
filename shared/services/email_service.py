#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared email service and configuration.

``EmailService.send_email`` is the one door every outbound email goes through,
and the ``mailer`` worker is the one process that ever talks to a mail
provider. A Web or Arena process charges the sending actor's budget and then
hands the fully rendered message to the worker over Valkey; it holds no SMTP
credentials and cannot send on its own. The only in-process path is the mock
provider, used when real sending is off (development, tests), where nothing
leaves the process.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol

from shared.queue_schema import MailJob
from shared.services.email_budget import EmailBudgetPolicy, EmailTier, check_email_budget
from shared.services.email_models import EmailMessage, EmailResult
from shared.services.email_providers import (
    EmailBudgetExceeded,
    EmailProvider,
    EmailProviderError,
    MockProvider,
    QueueProvider,
    SMTPProvider,
)
from shared.services.email_validation import EmailValidationService

QUEUE_PROVIDER_NAME = "queue"


class EmailSettings(Protocol):
    """Settings a producer (Web, Arena) needs to hand email to the mailer.

    Whether mail is really sent, and through what, is the mailer worker's
    decision (``NOCA_SEND_EMAIL``, ``NOCA_EMAIL_PROVIDER``, ``NOCA_SMTP_*``);
    a producer's settings never carry any of it.
    """

    EMAIL_SENDER: str
    EMAIL_SENDER_NAME: str | None
    BRAND_NAME: str
    EMAIL_QUEUE_JOB_TTL_SECONDS: int
    EMAIL_BUDGET_ENABLED: bool
    EMAIL_BUDGET_WINDOW_SECONDS: int
    EMAIL_BUDGET_USER_MAX: int
    EMAIL_BUDGET_ADMIN_MAX: int


@dataclass(frozen=True)
class EmailConfig:
    """Validated settings behind ``EmailService`` and the mailer's provider.

    A producer builds it from its own settings and uses only the sender
    identity, the job TTL and the budget. The mailer builds it from the worker
    settings and asks for the real provider through
    :meth:`create_worker_provider`, the one place that reads ``send_email``,
    ``provider_type`` and the SMTP fields.
    """

    send_email: bool = False
    provider_type: str = "smtp"
    default_from_email: str = "no-reply@noca.local"
    default_from_name: str | None = None
    smtp_server: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    mbox_log_dir: str | None = None
    queue_job_ttl_seconds: int = 3600
    budget: EmailBudgetPolicy | None = None

    VALID_PROVIDERS: ClassVar[frozenset[str]] = frozenset({"smtp", "mock"})

    def __post_init__(self) -> None:
        """Validate the settings every process needs."""
        provider = self.provider_type.casefold()
        if provider not in self.VALID_PROVIDERS:
            supported = ", ".join(sorted(self.VALID_PROVIDERS))
            raise ValueError(f"NOCA_EMAIL_PROVIDER must be one of: {supported}.")
        if self.queue_job_ttl_seconds <= 0:
            raise ValueError("NOCA_EMAIL_QUEUE_JOB_TTL_SECONDS must be positive.")
        if not self.default_from_email.strip():
            raise ValueError("NOCA_EMAIL_SENDER must be configured.")

    @property
    def worker_delivery_mode(self) -> str:
        """What the *mailer* does with a job: ``smtp`` for real, ``mock`` to its log."""
        if not self.send_email or self.provider_type.casefold() == "mock":
            return "mock"
        return "smtp"

    @classmethod
    def from_settings(cls, settings: EmailSettings) -> EmailConfig:
        """Build ``EmailConfig`` from a producer's or the mailer's settings.

        The provider and SMTP fields are read when present (the mailer) and
        left at their defaults otherwise (Web, Arena).
        """
        return cls(
            send_email=bool(getattr(settings, "SEND_EMAIL", False)),
            provider_type=str(getattr(settings, "EMAIL_PROVIDER", "smtp")),
            default_from_email=settings.EMAIL_SENDER,
            default_from_name=settings.EMAIL_SENDER_NAME or settings.BRAND_NAME,
            smtp_server=getattr(settings, "SMTP_SERVER", None),
            smtp_port=int(getattr(settings, "SMTP_PORT", 587)),
            smtp_username=getattr(settings, "SMTP_USERNAME", None),
            smtp_password=getattr(settings, "SMTP_PASSWORD", None),
            smtp_use_tls=bool(getattr(settings, "SMTP_USE_TLS", True)),
            mbox_log_dir=getattr(settings, "EMAIL_MBOX_LOG_DIR", None),
            queue_job_ttl_seconds=settings.EMAIL_QUEUE_JOB_TTL_SECONDS,
            budget=EmailBudgetPolicy(
                enabled=settings.EMAIL_BUDGET_ENABLED,
                window_seconds=settings.EMAIL_BUDGET_WINDOW_SECONDS,
                user_max=settings.EMAIL_BUDGET_USER_MAX,
                admin_max=settings.EMAIL_BUDGET_ADMIN_MAX,
            ),
        )

    def create_worker_provider(self) -> EmailProvider:
        """The provider the *mailer worker* delivers through -- the only SMTP in NOCA.

        Raises:
            ValueError: Real sending is on but the SMTP settings are incomplete.
        """
        if self.worker_delivery_mode == "mock":
            return MockProvider(log_emails=True)
        required = {
            "NOCA_SMTP_SERVER": self.smtp_server,
            "NOCA_SMTP_USERNAME": self.smtp_username,
            "NOCA_SMTP_PASSWORD": self.smtp_password,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required SMTP settings: {', '.join(missing)}")
        return SMTPProvider(
            smtp_server=str(self.smtp_server),
            smtp_port=self.smtp_port,
            username=str(self.smtp_username),
            password=str(self.smtp_password),
            use_tls=self.smtp_use_tls,
            mbox_log_dir=self.mbox_log_dir,
        )


class EmailService:
    """Application email sender service.

    With a Valkey runtime -- every real deployment -- the service is a pure
    producer: it charges the budget and queues the rendered message for the
    mailer, which alone decides whether to send it or log it. Without a
    runtime it keeps an in-process :class:`MockProvider` outbox instead: that
    is a test double, never a deployment mode, and it is logged as such.

    Args:
        config: Validated configuration.
        logger: Module logger.
        valkey_runtime: The module's Valkey runtime (``app.state.valkey_runtime``).
    """

    def __init__(self, config: EmailConfig, logger: logging.Logger, *, valkey_runtime: object | None = None) -> None:
        self._config = config
        self._logger = logger
        self._runtime = valkey_runtime
        self.default_from_email = config.default_from_email
        self.default_from_name = config.default_from_name
        if valkey_runtime is not None:
            self.delivery_mode = "queue"
            self.provider: EmailProvider = QueueProvider()
        else:
            self.delivery_mode = "mock"
            self.provider = MockProvider(log_emails=True)
            self._logger.warning("EmailService has no Valkey runtime: using the in-process mock outbox (tests only)")
        self._logger.debug(
            "EmailService initialized with delivery=%s provider=%s",
            self.delivery_mode,
            self.provider.get_provider_name(),
        )

    async def send_email(
        self,
        to_email: str,
        to_name: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
        subject: str | None = None,
        text_body: str | None = None,
        html_body: str | None = None,
        *,
        actor_key: str | None = None,
        tier: EmailTier = "user",
        **kwargs: Any,
    ) -> EmailResult:
        """Charge the actor's budget, then hand one rendered email to the mailer.

        Args:
            to_email: Recipient address.
            to_name: Recipient display name.
            from_email: Sender address; the configured default when omitted.
            from_name: Sender display name; the configured default when omitted.
            subject: Subject line.
            text_body: Plain-text body.
            html_body: HTML body.
            actor_key: Budget identity of whoever caused this email
                (``user:<id>``, ``admin:<id>``, ``ip:<addr>``, ``recipient:<addr>``).
                ``None`` means a system-originated email that is not budgeted.
            tier: Which budget ceiling applies to ``actor_key``.
            **kwargs: Extra ``EmailMessage`` fields (``cc_email``, ``cc_name``).

        Returns:
            A successful result whose ``provider`` is ``"queue"`` and whose
            ``message_id`` is the job id -- "accepted for delivery", not "sent".
            (Without a runtime: the mock outbox's own result.)

        Raises:
            EmailBudgetExceeded: The actor's budget is spent; nothing was queued.
            EmailProviderError: The mail queue could not take the job (nothing
                is buffered; the message was not accepted).
        """
        message = EmailMessage(
            to_email=to_email,
            to_name=to_name,
            from_email=from_email or self.default_from_email,
            from_name=from_name or self.default_from_name,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            **kwargs,
        )
        if actor_key is not None and self._config.budget is not None and self._runtime is not None:
            retry_after = await check_email_budget(
                self._runtime, actor_key=actor_key, tier=tier, policy=self._config.budget
            )
            if retry_after:
                self._logger.warning("Email budget exceeded for %s (%s); refusing to send", actor_key, tier)
                raise EmailBudgetExceeded(actor_key=actor_key, retry_after_seconds=retry_after)

        if self.delivery_mode == "queue":
            return await self._enqueue(message, actor_key=actor_key)

        # Mock outbox (no runtime; tests only): nothing leaves the process, but keep
        # it off the event loop so the contract matches the worker's own delivery step.
        result = await asyncio.to_thread(self.provider.send, message)
        self._logger.debug("Email recorded by the mock provider to=%s subject=%s", to_email, subject)
        return result

    async def _enqueue(self, message: EmailMessage, *, actor_key: str | None) -> EmailResult:
        job = MailJob.from_message(message, actor_key=actor_key, enqueued_at=time.time())
        runtime: Any = self._runtime
        try:
            await runtime.enqueue_mail_job(job, ttl_seconds=self._config.queue_job_ttl_seconds)
        except Exception as exc:
            # Nothing was buffered: the message is gone unless the caller acts.
            # Reported as a provider failure so every "not sent" path applies.
            raise EmailProviderError(f"Mail queue unavailable: {exc}") from exc
        self._logger.debug("Email queued job_id=%s to=%s subject=%s", job.job_id, message.to_email, message.subject)
        return EmailResult(
            success=True,
            provider=QUEUE_PROVIDER_NAME,
            message_id=job.job_id,
            to=message.to_email,
            sent_at=datetime.now(UTC).isoformat(),
        )

    def get_provider_info(self) -> dict[str, str | None]:
        """Expose current provider metadata."""
        return {
            "provider_name": self.provider.get_provider_name(),
            "delivery_mode": self.delivery_mode,
            "default_from": self.default_from_email,
            "default_from_name": self.default_from_name,
        }


__all__ = ["QUEUE_PROVIDER_NAME", "EmailConfig", "EmailService", "EmailSettings", "EmailValidationService"]
