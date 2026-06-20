#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared email provider contracts and implementations."""

import logging
import smtplib
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from email.charset import QP, Charset
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from pathlib import Path
from typing import Any

from shared.services import email_mbox_log
from shared.services.email_models import EmailMessage, EmailResult, build_rfc5322_address

logger = logging.getLogger(__name__)

# Quoted-printable charset for plain-text parts; HTML parts stay base64.
_UTF8_QP = Charset("utf-8")
_UTF8_QP.body_encoding = QP


class EmailProviderError(Exception):
    """Base exception for email provider errors."""


class EmailProvider(ABC):
    """Base contract implemented by email providers."""

    @abstractmethod
    def send(self, message: EmailMessage) -> EmailResult:
        """Send an email message.

        Args:
            message: Email payload.

        Returns:
            Provider result metadata.
        """

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return a human-readable provider name."""


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


def _build_recipient(name: str | None, email: str) -> str:
    """Build an RFC 5322-compliant recipient string when possible."""
    return build_rfc5322_address(name, email)


class SMTPProvider(EmailProvider):
    """SMTP provider implementation."""

    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        username: str,
        password: str,
        use_tls: bool = True,
        mbox_log_dir: str | None = None,
    ) -> None:
        """Build an SMTP provider instance.

        Args:
            smtp_server: SMTP server hostname.
            smtp_port: SMTP server port.
            username: SMTP account username.
            password: SMTP account password.
            use_tls: Whether to call STARTTLS before authentication.
            mbox_log_dir: Directory for the mbox audit log; ``None`` disables it.
        """
        self._smtp_server = smtp_server
        self._smtp_port = smtp_port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._mbox_log_dir = mbox_log_dir

    def send(self, message: EmailMessage) -> EmailResult:
        """Send message via SMTP."""
        try:
            mime_message = MIMEMultipart("alternative")
            if message.from_email:
                mime_message["From"] = _build_recipient(message.from_name, message.from_email)
            mime_message["To"] = _build_recipient(message.to_name, str(message.to_email))
            if message.cc_email:
                mime_message["Cc"] = _build_recipient(message.cc_name, message.cc_email)
            mime_message["Subject"] = message.subject or ""
            # Set the Message-ID before sending so the delivered message and the
            # mbox copy share it.
            msg_id = make_msgid()
            mime_message["Message-ID"] = msg_id

            if message.text_body:
                # MIMEText accepts a Charset at runtime (it is forwarded to
                # set_payload); typeshed types the arg as str | None only.
                plain_part = MIMEText(message.text_body, "plain", _UTF8_QP)  # type: ignore[arg-type]
                mime_message.attach(plain_part)
            if message.html_body:
                mime_message.attach(MIMEText(message.html_body, "html", "utf-8"))

            recipients = [str(message.to_email)]
            if message.cc_email:
                recipients.append(message.cc_email)

            relay = f"{self._smtp_server}:{self._smtp_port}"
            with smtplib.SMTP(self._smtp_server, self._smtp_port) as server:
                if self._use_tls:
                    server.starttls()
                server.login(self._username, self._password)
                # send_message() returns a dict of *refused* recipients (empty
                # when all accepted); it raises SMTPRecipientsRefused if every
                # recipient is refused.
                raw_response = server.send_message(mime_message, to_addrs=recipients)
                greeting = server.ehlo_resp
                if greeting:
                    relay = f"{relay} ({greeting.decode('utf-8', 'replace').splitlines()[0]})"

            sent_at = _utcnow()
            accepted = [r for r in recipients if r not in raw_response]
            self._log_to_mbox(mime_message, message.from_email, relay, accepted, sent_at)

            return EmailResult(
                success=True,
                provider="smtp",
                message_id=msg_id,
                to=str(message.to_email),
                sent_at=sent_at.isoformat(),
                raw_response=raw_response,
            )
        except Exception as exc:  # noqa: BLE001
            raise EmailProviderError(f"Error sending email via SMTP: {exc}") from exc

    def _log_to_mbox(
        self,
        mime_message: MIMEMultipart,
        sender: str | None,
        relay: str,
        recipients: list[str],
        when: datetime,
    ) -> None:
        """Append the delivered message to the mbox audit log when configured."""
        if not self._mbox_log_dir or not recipients:
            return
        email_mbox_log.append_message(
            Path(self._mbox_log_dir),
            mime_message,
            sender=sender or "",
            relay=relay,
            recipients=recipients,
            when=when,
        )

    def get_provider_name(self) -> str:
        """Return provider display name."""
        return f"SMTP ({self._smtp_server})"


class MockProvider(EmailProvider):
    """Provider used for development and tests."""

    def __init__(self, log_emails: bool = True) -> None:
        """Build a mock provider.

        Args:
            log_emails: Whether to log payloads at info level.
        """
        self.log_emails = log_emails
        self.sent_emails: list[dict[str, Any]] = []

    def send(self, message: EmailMessage) -> EmailResult:
        """Store and optionally log a fake email send."""
        message_id = str(uuid.uuid4())
        sent_at = _utcnow().isoformat()
        email_info = {
            "message_id": message_id,
            "from": _build_recipient(message.from_name, str(message.from_email)) if message.from_email else "",
            "to": _build_recipient(message.to_name, str(message.to_email)),
            "cc": _build_recipient(message.cc_name, message.cc_email) if message.cc_email else "",
            "subject": message.subject,
            "text_body": message.text_body,
            "html_body": message.html_body,
            "sent_at": sent_at,
        }
        self.sent_emails.append(email_info)

        if self.log_emails:
            logger.info("=== MOCK EMAIL ===")
            logger.info("From: %s", message.from_email)
            logger.info("To: %s", message.to_email)
            logger.info("Subject: %s", message.subject)
            logger.info("--- Text Body ---")
            logger.info(message.text_body or "(empty)")
            if message.html_body:
                logger.info("--- HTML Body ---")
                logger.info(message.html_body)
            logger.info("==================")

        return EmailResult(
            success=True,
            provider="mock",
            message_id=message_id,
            to=message.to_email,
            sent_at=sent_at,
            raw_response=email_info,
        )

    def get_provider_name(self) -> str:
        """Return provider display name."""
        return "Mock (Development)"

    def get_sent_emails(self) -> list[dict[str, Any]]:
        """Return sent email payloads for tests."""
        return self.sent_emails.copy()

    def clear_sent_emails(self) -> None:
        """Clear stored sent email payloads."""
        self.sent_emails.clear()
