import logging

import pytest

from shared.services.email_service import EmailConfig, EmailService, EmailValidationService
from web.services.user_credentials_email_service import (
    build_user_credentials_email_content,
    send_user_credentials_email,
)


def test_email_validation_service_accepts_valid_email() -> None:
    assert EmailValidationService.is_valid("Team+One@Example.com") is True
    assert EmailValidationService.normalize("Team+One@Example.com") == "team+one@example.com"


def test_email_validation_service_rejects_invalid_email() -> None:
    assert EmailValidationService.is_valid("invalid-email") is False
    with pytest.raises(ValueError):
        EmailValidationService.normalize("invalid-email")


def test_canonicalize_strips_plus_tag() -> None:
    assert EmailValidationService.canonicalize("Daniel+test@Lobato.org") == "daniel@lobato.org"


def test_canonicalize_strips_local_part_dots() -> None:
    assert EmailValidationService.canonicalize("joe.smith@gmail.com") == "joesmith@gmail.com"
    assert EmailValidationService.canonicalize("j.o.e.s.m.i.t.h@gmail.com") == "joesmith@gmail.com"


def test_canonicalize_strips_plus_tag_and_dots_together() -> None:
    assert EmailValidationService.canonicalize("d.a.niel+promo@lobato.org") == "daniel@lobato.org"


def test_canonicalize_aliases_collapse_to_same_root() -> None:
    root = EmailValidationService.canonicalize("user@example.com")
    assert EmailValidationService.canonicalize("u.s.e.r@example.com") == root
    assert EmailValidationService.canonicalize("user+1@example.com") == root
    assert EmailValidationService.canonicalize("u.ser+2@example.com") == root


def test_canonicalize_is_idempotent() -> None:
    once = EmailValidationService.canonicalize("d.a.niel+x@lobato.org")
    assert EmailValidationService.canonicalize(once) == once


def test_canonicalize_empty_local_falls_back_to_local_part() -> None:
    # "+tag@x" has an empty canonical local; fall back to the normalised local.
    assert EmailValidationService.canonicalize("+tag@example.com") == "+tag@example.com"


def test_canonicalize_rejects_invalid_email() -> None:
    with pytest.raises(ValueError):
        EmailValidationService.canonicalize("invalid-email")


def test_email_config_requires_smtp_fields_when_enabled() -> None:
    with pytest.raises(ValueError, match="Missing required SMTP settings"):
        EmailConfig(
            send_email=True,
            provider_type="smtp",
            default_from_email="no-reply@example.com",
            default_from_name="NOCA",
            smtp_server=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_use_tls=True,
        )


def test_email_service_uses_mock_provider_when_send_disabled(caplog: pytest.LogCaptureFixture) -> None:
    config = EmailConfig(
        send_email=False,
        provider_type="smtp",
        default_from_email="no-reply@example.com",
        default_from_name="NOCA",
        smtp_server="smtp.example.com",
        smtp_port=587,
        smtp_username="username",
        smtp_password="password",
        smtp_use_tls=True,
    )
    with caplog.at_level(logging.DEBUG, logger="test-email"):
        service = EmailService(config=config, logger=logging.getLogger("test-email"))

    result = service.send_email(
        to_email="recipient@example.com",
        subject="Hello",
        text_body="Body",
    )

    assert result.success is True
    assert result.provider == "mock"
    assert result.to == "recipient@example.com"
    assert "provider=Mock (Development) mbox_logging_enabled=False mbox_log_dir=N/A" in caplog.text


def test_email_service_logs_enabled_mbox_directory(caplog: pytest.LogCaptureFixture) -> None:
    config = EmailConfig(
        send_email=True,
        provider_type="smtp",
        default_from_email="no-reply@example.com",
        default_from_name="NOCA",
        smtp_server="smtp.example.com",
        smtp_port=587,
        smtp_username="username",
        smtp_password="password",
        smtp_use_tls=True,
        mbox_log_dir="/var/log/noca/email",
    )

    with caplog.at_level(logging.DEBUG, logger="test-email"):
        EmailService(config=config, logger=logging.getLogger("test-email"))

    assert (
        "provider=SMTP (smtp.example.com) mbox_logging_enabled=True mbox_log_dir=/var/log/noca/email"
    ) in caplog.text


def test_build_user_credentials_email_content_uses_expected_template() -> None:
    content = build_user_credentials_email_content(
        fullname="Alice Smith",
        contest_name="Contest 2026",
        contest_login_url="https://example.com/c/contest-2026/login",
        username="alice",
        password="Password123!",
        sender_name="NOCA Team",
    )

    assert content.subject == "Your credentials for contest Contest 2026"
    assert "Yo, Alice Smith!" in content.text_body
    assert (
        "These are your credentials for connecting on contest Contest 2026 running on Noca Contest."
        in content.text_body
    )
    assert "Login page: https://example.com/c/contest-2026/login" in content.text_body
    assert "username: alice" in content.text_body
    assert "password: Password123!" in content.text_body
    assert "Looking forward to see you! Best," in content.text_body
    assert content.text_body.endswith("NOCA Team")


def test_send_user_credentials_email_returns_success_with_mock_provider() -> None:
    config = EmailConfig(
        send_email=False,
        provider_type="smtp",
        default_from_email="no-reply@example.com",
        default_from_name="NOCA",
        smtp_server="smtp.example.com",
        smtp_port=587,
        smtp_username="username",
        smtp_password="password",
        smtp_use_tls=True,
    )
    service = EmailService(config=config, logger=logging.getLogger("test-email"))

    result = send_user_credentials_email(
        service,
        to_email="team@example.com",
        fullname="Team One",
        contest_name="Regional 2026",
        contest_login_url="https://example.com/c/regional-2026/login",
        username="team1",
        password="StrongPass2!",
    )

    assert result.success is True
    assert result.detail == "Credentials email sent successfully."
