#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared email service and configuration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from shared.services.email_models import EmailMessage, EmailResult
from shared.services.email_providers import EmailProvider, MockProvider, SMTPProvider
from shared.services.email_validation import EmailValidationService


class EmailSettings(Protocol):
    """Settings contract required to build ``EmailConfig``."""

    SEND_EMAIL: bool
    EMAIL_PROVIDER: str
    EMAIL_SENDER: str
    EMAIL_SENDER_NAME: str | None
    APP_NAME: str
    SMTP_SERVER: str | None
    SMTP_PORT: int
    SMTP_USERNAME: str | None
    SMTP_PASSWORD: str | None
    SMTP_USE_TLS: bool
    EMAIL_MBOX_LOG_DIR: str | None


@dataclass(frozen=True)
class EmailConfig:
    """Validated settings required by ``EmailService``."""

    send_email: bool
    provider_type: str
    default_from_email: str
    default_from_name: str | None
    smtp_server: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_use_tls: bool
    mbox_log_dir: str | None = None

    VALID_PROVIDERS: ClassVar[frozenset[str]] = frozenset({"smtp", "mock"})

    def __post_init__(self) -> None:
        """Validate provider configuration."""
        provider = self.provider_type.casefold()
        if provider not in self.VALID_PROVIDERS:
            supported = ", ".join(sorted(self.VALID_PROVIDERS))
            raise ValueError(f"NOCA_EMAIL_PROVIDER must be one of: {supported}.")

        if not self.default_from_email.strip():
            raise ValueError("NOCA_EMAIL_SENDER must be configured.")

        if not self.send_email or provider == "mock":
            return

        required = {
            "NOCA_SMTP_SERVER": self.smtp_server,
            "NOCA_SMTP_USERNAME": self.smtp_username,
            "NOCA_SMTP_PASSWORD": self.smtp_password,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required SMTP settings: {', '.join(missing)}")

    @classmethod
    def from_settings(cls, settings: EmailSettings) -> EmailConfig:
        """Build ``EmailConfig`` from compatible settings."""
        return cls(
            send_email=settings.SEND_EMAIL,
            provider_type=settings.EMAIL_PROVIDER,
            default_from_email=settings.EMAIL_SENDER,
            default_from_name=settings.EMAIL_SENDER_NAME or settings.APP_NAME,
            smtp_server=settings.SMTP_SERVER,
            smtp_port=settings.SMTP_PORT,
            smtp_username=settings.SMTP_USERNAME,
            smtp_password=settings.SMTP_PASSWORD,
            smtp_use_tls=settings.SMTP_USE_TLS,
            mbox_log_dir=settings.EMAIL_MBOX_LOG_DIR,
        )

    def create_provider(self) -> EmailProvider:
        """Instantiate configured provider."""
        provider = self.provider_type.casefold()
        if not self.send_email or provider == "mock":
            return MockProvider(log_emails=True)
        if provider == "smtp":
            return SMTPProvider(
                smtp_server=str(self.smtp_server),
                smtp_port=self.smtp_port,
                username=str(self.smtp_username),
                password=str(self.smtp_password),
                use_tls=self.smtp_use_tls,
                mbox_log_dir=self.mbox_log_dir,
            )
        raise ValueError(f"Unsupported email provider: {self.provider_type}")


class EmailService:
    """Application email sender service."""

    def __init__(self, config: EmailConfig, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.provider = config.create_provider()
        self.default_from_email = config.default_from_email
        self.default_from_name = config.default_from_name
        mbox_logging_enabled = isinstance(self.provider, SMTPProvider) and bool(config.mbox_log_dir)
        self._logger.debug(
            "EmailService initialized with provider=%s mbox_logging_enabled=%s mbox_log_dir=%s",
            self.provider.get_provider_name(),
            mbox_logging_enabled,
            config.mbox_log_dir or "N/A",
        )

    def send_email(
        self,
        to_email: str,
        to_name: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
        subject: str | None = None,
        text_body: str | None = None,
        html_body: str | None = None,
        **kwargs: Any,
    ) -> EmailResult:
        """Send an email through the configured provider."""
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
        result = self.provider.send(message)
        self._logger.debug(
            "Email sent via provider=%s to=%s subject=%s message_id=%s",
            self.provider.get_provider_name(),
            to_email,
            subject,
            result.message_id or "N/A",
        )
        return result

    def get_provider_info(self) -> dict[str, str | None]:
        """Expose current provider metadata."""
        return {
            "provider_name": self.provider.get_provider_name(),
            "default_from": self.default_from_email,
            "default_from_name": self.default_from_name,
        }


__all__ = ["EmailConfig", "EmailService", "EmailSettings", "EmailValidationService"]
