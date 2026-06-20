#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for shared.services.email_models and shared.services.email_providers."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from shared.services.email_models import EmailMessage, EmailResult, build_rfc5322_address
from shared.services.email_providers import EmailProviderError, MockProvider, SMTPProvider

# ---------------------------------------------------------------------------
# build_rfc5322_address
# ---------------------------------------------------------------------------


def test_rfc5322_with_display_name() -> None:
    result = build_rfc5322_address("John Doe", "john@example.com")
    assert "John Doe" in result
    assert "john@example.com" in result


def test_rfc5322_with_none_name_returns_bare_email() -> None:
    result = build_rfc5322_address(None, "nobody@example.com")
    assert result == "nobody@example.com"


def test_rfc5322_with_empty_name_returns_bare_email() -> None:
    result = build_rfc5322_address("", "nobody@example.com")
    assert result == "nobody@example.com"


def test_rfc5322_falls_back_to_bare_email_on_invalid_name() -> None:
    # A name containing a newline triggers an Address ValueError.
    result = build_rfc5322_address("Bad\nName", "x@example.com")
    assert result == "x@example.com"


# ---------------------------------------------------------------------------
# EmailMessage validation
# ---------------------------------------------------------------------------


def test_email_message_requires_to_email() -> None:
    with pytest.raises(ValueError, match="to_email"):
        EmailMessage(text_body="hello")


def test_email_message_requires_body() -> None:
    with pytest.raises(ValueError):
        EmailMessage(to_email="a@b.com")


def test_email_message_accepts_text_only() -> None:
    msg = EmailMessage(to_email="a@b.com", text_body="hi")
    assert msg.text_body == "hi"
    assert msg.html_body is None


def test_email_message_accepts_html_only() -> None:
    msg = EmailMessage(to_email="a@b.com", html_body="<p>hi</p>")
    assert msg.html_body == "<p>hi</p>"
    assert msg.text_body is None


def test_email_message_accepts_both_bodies() -> None:
    msg = EmailMessage(to_email="a@b.com", text_body="hi", html_body="<p>hi</p>")
    assert msg.text_body and msg.html_body


def test_email_message_is_frozen() -> None:
    msg = EmailMessage(to_email="a@b.com", text_body="hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.subject = "new"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EmailResult
# ---------------------------------------------------------------------------


def test_email_result_success_construction() -> None:
    result = EmailResult(success=True, provider="test", message_id="id1", to="a@b.com")
    assert result.success is True
    assert result.provider == "test"


def test_email_result_defaults_are_none() -> None:
    result = EmailResult(success=False)
    assert result.message_id is None
    assert result.to is None
    assert result.sent_at is None
    assert result.error_code is None
    assert result.raw_response is None


def test_email_result_is_frozen() -> None:
    result = EmailResult(success=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MockProvider
# ---------------------------------------------------------------------------


def _make_message(
    to: str = "to@example.com",
    text: str = "hello",
    **kwargs,
) -> EmailMessage:
    return EmailMessage(to_email=to, text_body=text, **kwargs)


def test_mock_provider_name() -> None:
    assert MockProvider().get_provider_name() == "Mock (Development)"


def test_mock_send_returns_success_result() -> None:
    result = MockProvider().send(_make_message())
    assert result.success is True
    assert result.provider == "mock"
    assert result.message_id is not None
    assert result.to == "to@example.com"
    assert result.sent_at is not None


def test_mock_send_stores_email() -> None:
    provider = MockProvider()
    provider.send(_make_message(subject="Hello"))
    emails = provider.get_sent_emails()
    assert len(emails) == 1
    assert emails[0]["subject"] == "Hello"


def test_mock_get_sent_emails_returns_copy() -> None:
    provider = MockProvider()
    provider.send(_make_message())
    copy = provider.get_sent_emails()
    copy.clear()
    assert len(provider.get_sent_emails()) == 1


def test_mock_clear_sent_emails() -> None:
    provider = MockProvider()
    provider.send(_make_message())
    provider.clear_sent_emails()
    assert provider.get_sent_emails() == []


def test_mock_multiple_sends_accumulate() -> None:
    provider = MockProvider(log_emails=False)
    for i in range(3):
        provider.send(_make_message(subject=f"msg-{i}"))
    emails = provider.get_sent_emails()
    assert len(emails) == 3
    assert emails[0]["subject"] == "msg-0"
    assert emails[2]["subject"] == "msg-2"


def test_mock_log_emails_false_suppresses_logs(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    provider = MockProvider(log_emails=False)
    with caplog.at_level(logging.INFO, logger="shared.services.email_providers"):
        provider.send(_make_message())
    assert not any("MOCK EMAIL" in r.message for r in caplog.records)


def test_mock_log_emails_true_produces_logs(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    provider = MockProvider(log_emails=True)
    with caplog.at_level(logging.INFO, logger="shared.services.email_providers"):
        provider.send(_make_message(subject="Log me"))
    assert any("MOCK EMAIL" in r.message for r in caplog.records)


def test_mock_payload_keys() -> None:
    provider = MockProvider(log_emails=False)
    provider.send(_make_message(from_email="from@example.com", cc_email="cc@example.com"))
    payload = provider.get_sent_emails()[0]
    for key in ("message_id", "from", "to", "cc", "subject", "text_body", "html_body", "sent_at"):
        assert key in payload


def test_mock_payload_from_empty_when_no_from_email() -> None:
    provider = MockProvider(log_emails=False)
    provider.send(_make_message())
    assert provider.get_sent_emails()[0]["from"] == ""


def test_mock_payload_cc_empty_when_no_cc_email() -> None:
    provider = MockProvider(log_emails=False)
    provider.send(_make_message())
    assert provider.get_sent_emails()[0]["cc"] == ""


# ---------------------------------------------------------------------------
# SMTPProvider
# ---------------------------------------------------------------------------


def _smtp_provider() -> SMTPProvider:
    return SMTPProvider(
        smtp_server="smtp.example.com",
        smtp_port=587,
        username="user",
        password="pass",
        use_tls=True,
    )


def _patched_smtp():
    """Context manager that patches smtplib.SMTP and returns the mock server."""
    return patch("smtplib.SMTP")


def test_smtp_provider_name() -> None:
    provider = _smtp_provider()
    assert provider.get_provider_name() == "SMTP (smtp.example.com)"


def test_smtp_send_returns_success() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        result = _smtp_provider().send(_make_message())
    assert result.success is True
    assert result.provider == "smtp"
    assert result.message_id is not None
    assert result.to == "to@example.com"


def test_smtp_calls_starttls_when_tls_enabled() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message())
    mock_server.starttls.assert_called_once()


def test_smtp_skips_starttls_when_tls_disabled() -> None:
    provider = SMTPProvider("smtp.example.com", 25, "u", "p", use_tls=False)
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        provider.send(_make_message())
    mock_server.starttls.assert_not_called()


def test_smtp_calls_login() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message())
    mock_server.login.assert_called_once_with("user", "pass")


def test_smtp_sets_to_and_subject_headers() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message(subject="Test subject"))
    call_args = mock_server.send_message.call_args
    mime_msg = call_args[0][0]
    assert mime_msg["Subject"] == "Test subject"
    assert "to@example.com" in mime_msg["To"]


def test_smtp_sets_from_header_when_from_email_present() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message(from_email="sender@example.com"))
    mime_msg = mock_server.send_message.call_args[0][0]
    assert mime_msg["From"] is not None


def test_smtp_omits_from_header_when_from_email_absent() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message())
    mime_msg = mock_server.send_message.call_args[0][0]
    assert mime_msg["From"] is None


def test_smtp_sets_cc_header_when_cc_present() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message(cc_email="cc@example.com"))
    mime_msg = mock_server.send_message.call_args[0][0]
    assert "cc@example.com" in mime_msg["Cc"]


def test_smtp_omits_cc_header_when_cc_absent() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message())
    mime_msg = mock_server.send_message.call_args[0][0]
    assert mime_msg["Cc"] is None


def test_smtp_attaches_text_part() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message(text="plain text"))
    mime_msg = mock_server.send_message.call_args[0][0]
    payloads = [part.get_payload(decode=True).decode() for part in mime_msg.get_payload()]
    assert any("plain text" in p for p in payloads)


def test_smtp_plain_part_is_quoted_printable() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message(text="plain text"))
    mime_msg = mock_server.send_message.call_args[0][0]
    plain = next(p for p in mime_msg.get_payload() if p.get_content_type() == "text/plain")
    assert plain.get("content-transfer-encoding") == "quoted-printable"
    assert "plain text" in plain.get_payload()


def test_smtp_attaches_html_part() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.send_message.return_value = {}
        _smtp_provider().send(EmailMessage(to_email="a@b.com", html_body="<p>hi</p>"))
    mime_msg = mock_server.send_message.call_args[0][0]
    payloads = [part.get_payload(decode=True).decode() for part in mime_msg.get_payload()]
    assert any("<p>hi</p>" in p for p in payloads)


def test_smtp_wraps_exception_in_email_provider_error() -> None:
    with _patched_smtp() as mock_cls:
        mock_cls.return_value.__enter__.side_effect = ConnectionRefusedError("refused")
        with pytest.raises(EmailProviderError, match="SMTP"):
            _smtp_provider().send(_make_message())


def test_email_provider_error_is_exception() -> None:
    assert issubclass(EmailProviderError, Exception)


# ---------------------------------------------------------------------------
# SMTPProvider Message-ID and mbox audit logging
# ---------------------------------------------------------------------------


def test_smtp_sets_message_id_matching_result() -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.ehlo_resp = None
        mock_server.send_message.return_value = {}
        result = _smtp_provider().send(_make_message())
    mime_msg = mock_server.send_message.call_args[0][0]
    assert mime_msg["Message-ID"] is not None
    assert mime_msg["Message-ID"] == result.message_id


def _mbox_provider(mbox_dir: object) -> SMTPProvider:
    return SMTPProvider(
        smtp_server="smtp.example.com",
        smtp_port=587,
        username="user",
        password="pass",
        use_tls=True,
        mbox_log_dir=str(mbox_dir),
    )


def _read_mbox(mbox_dir) -> list:
    import mailbox

    paths = list(mbox_dir.glob("*.mbox"))
    if not paths:
        return []
    box = mailbox.mbox(paths[0])
    messages = list(box)
    box.close()
    return messages


def test_smtp_logs_to_mbox_after_acceptance(tmp_path) -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.ehlo_resp = None
        mock_server.send_message.return_value = {}
        result = _mbox_provider(tmp_path).send(_make_message(from_email="from@example.com"))
    messages = _read_mbox(tmp_path)
    assert len(messages) == 1
    assert messages[0]["Message-ID"] == result.message_id
    assert "smtp.example.com:587" in messages[0]["X-NOCA-SMTP-Relay"]
    assert messages[0]["X-NOCA-Recipients"] == "to@example.com"


def test_smtp_does_not_log_on_failure(tmp_path) -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.ehlo_resp = None
        mock_server.send_message.side_effect = OSError("boom")
        with pytest.raises(EmailProviderError):
            _mbox_provider(tmp_path).send(_make_message())
    assert _read_mbox(tmp_path) == []


def test_smtp_logs_only_accepted_recipients(tmp_path) -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.ehlo_resp = None
        # cc@example.com refused; to@example.com accepted.
        mock_server.send_message.return_value = {"cc@example.com": (550, b"no")}
        _mbox_provider(tmp_path).send(_make_message(cc_email="cc@example.com"))
    messages = _read_mbox(tmp_path)
    assert messages[0]["X-NOCA-Recipients"] == "to@example.com"


def test_smtp_logging_failure_does_not_break_delivery(tmp_path) -> None:
    # A failure inside the logger (here: mbox open) must be swallowed so a
    # successful delivery is still reported as success.
    with (
        _patched_smtp() as mock_cls,
        patch("shared.services.email_mbox_log.mailbox.mbox", side_effect=OSError("disk full")),
    ):
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.ehlo_resp = None
        mock_server.send_message.return_value = {}
        result = _mbox_provider(tmp_path).send(_make_message())
    assert result.success is True


def test_smtp_no_mbox_when_dir_unset(tmp_path) -> None:
    with _patched_smtp() as mock_cls:
        mock_server = mock_cls.return_value.__enter__.return_value
        mock_server.ehlo_resp = None
        mock_server.send_message.return_value = {}
        _smtp_provider().send(_make_message())
    assert _read_mbox(tmp_path) == []


# ---------------------------------------------------------------------------
# EmailConfig wiring of mbox_log_dir
# ---------------------------------------------------------------------------


def _email_settings(mbox_dir: str | None):
    import types

    return types.SimpleNamespace(
        SEND_EMAIL=True,
        EMAIL_PROVIDER="smtp",
        EMAIL_SENDER="no-reply@noca.local",
        EMAIL_SENDER_NAME=None,
        APP_NAME="NOCA",
        SMTP_SERVER="smtp.example.com",
        SMTP_PORT=587,
        SMTP_USERNAME="user",
        SMTP_PASSWORD="pass",
        SMTP_USE_TLS=True,
        EMAIL_MBOX_LOG_DIR=mbox_dir,
    )


def test_email_config_passes_mbox_log_dir_to_provider() -> None:
    from shared.services.email_service import EmailConfig

    config = EmailConfig.from_settings(_email_settings("/var/log/noca/email"))
    assert config.mbox_log_dir == "/var/log/noca/email"
    provider = config.create_provider()
    assert isinstance(provider, SMTPProvider)
    assert provider._mbox_log_dir == "/var/log/noca/email"


def test_email_config_default_mbox_log_dir_is_none() -> None:
    from shared.services.email_service import EmailConfig

    config = EmailConfig(
        send_email=True,
        provider_type="smtp",
        default_from_email="no-reply@noca.local",
        default_from_name=None,
        smtp_server="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="pass",
        smtp_use_tls=True,
    )
    assert config.mbox_log_dir is None
