#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Mailer worker settings: URLs, pace, and the cross-field validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mailer.config import Settings

_BASE = {
    "DB_USER": "u",
    "DB_PASSWORD": "p",
    "DB_SERVER": "db",
    "DB_NAME": "noca",
    "VALKEY_SERVER": "127.0.0.1",
    "VALKEY_DB": 0,
    "VALKEY_USER": None,
    "VALKEY_PASSWORD": None,
}


def _settings(**env: object) -> Settings:
    """Build settings from init values (which outrank the test session's environment)."""
    values = {**_BASE, **{key.removeprefix("NOCA_"): value for key, value in env.items()}}
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_worker_defaults_and_pace() -> None:
    s = _settings()
    assert s.MAILER_MAX_PER_MINUTE == 60 and s.send_interval_seconds == 1.0
    assert s.MAILER_POLL_INTERVAL_SECONDS == 2.0
    assert s.MAILER_MAX_REQUEUE_COUNT == 3
    assert s.EMAIL_QUEUE_JOB_TTL_SECONDS == 3600
    assert s.MAILER_HEARTBEAT_FILE == "/tmp/mailer-heartbeat"
    assert s.db_url == "postgresql+asyncpg://u:p@db:5432/noca"
    assert s.valkey_url == "redis://127.0.0.1:6379/0"


def test_pace_follows_max_per_minute() -> None:
    assert _settings(NOCA_MAILER_MAX_PER_MINUTE="120").send_interval_seconds == 0.5


def test_valkey_url_with_credentials() -> None:
    assert _settings(NOCA_VALKEY_PASSWORD="s").valkey_url == "redis://:s@127.0.0.1:6379/0"
    assert _settings(NOCA_VALKEY_USER="a", NOCA_VALKEY_PASSWORD="s").valkey_url == "redis://a:s@127.0.0.1:6379/0"


def test_presence_ttl_must_exceed_interval() -> None:
    with pytest.raises(ValidationError, match="MAILER_PRESENCE_TTL_SECONDS"):
        _settings(NOCA_MAILER_PRESENCE_INTERVAL_SECONDS="60", NOCA_MAILER_PRESENCE_TTL_SECONDS="60")


def test_heartbeat_file_must_be_absolute_and_stale_must_exceed_interval() -> None:
    with pytest.raises(ValidationError, match="absolute"):
        _settings(NOCA_MAILER_HEARTBEAT_FILE="relative/heartbeat")
    with pytest.raises(ValidationError, match="STALE"):
        _settings(NOCA_MAILER_HEARTBEAT_INTERVAL_SECONDS="30", NOCA_MAILER_HEARTBEAT_STALE_SECONDS="30")


def test_nonce_ttl_must_exceed_freshness() -> None:
    with pytest.raises(ValidationError, match="NONCE_TTL"):
        _settings(NOCA_MAILER_WORKER_COMMAND_FRESHNESS_SECONDS="60", NOCA_MAILER_WORKER_COMMAND_NONCE_TTL_SECONDS="60")


def test_email_settings_satisfy_the_shared_contract() -> None:
    from shared.services.email_service import EmailConfig

    config = EmailConfig.from_settings(_settings(NOCA_EMAIL_SENDER="mail@test.example"))
    assert config.default_from_email == "mail@test.example"
    assert config.default_from_name == "NOCA"
    assert config.worker_delivery_mode == "mock"
    assert config.create_worker_provider().get_provider_name() == "Mock (Development)"


# ---------------------------------------------------------------------------
# Provider settings: the mailer alone validates them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\t"])
def test_mbox_log_dir_empty_or_blank_is_disabled(value: str | None) -> None:
    assert Settings.normalize_mbox_log_dir(value) is None


def test_mbox_log_dir_accepts_and_strips_absolute_paths_and_rejects_relative_ones() -> None:
    assert Settings.normalize_mbox_log_dir("/var/log/noca/email") == "/var/log/noca/email"
    assert Settings.normalize_mbox_log_dir("  /var/log/noca/email  ") == "/var/log/noca/email"
    with pytest.raises(ValueError, match="absolute path"):
        Settings.normalize_mbox_log_dir("relative/path")


def test_real_smtp_sending_requires_the_relay_and_credentials() -> None:
    with pytest.raises(ValidationError, match="Missing required SMTP settings"):
        _settings(NOCA_SEND_EMAIL=True, NOCA_EMAIL_PROVIDER="smtp")
    with pytest.raises(ValidationError, match="NOCA_EMAIL_PROVIDER"):
        _settings(NOCA_EMAIL_PROVIDER="pigeon")
    # Sending off, or the mock provider, needs nothing.
    assert _settings(NOCA_SEND_EMAIL=False).SMTP_SERVER is None
    assert _settings(NOCA_SEND_EMAIL=True, NOCA_EMAIL_PROVIDER="mock").EMAIL_PROVIDER == "mock"
    ok = _settings(
        NOCA_SEND_EMAIL=True,
        NOCA_EMAIL_PROVIDER="smtp",
        NOCA_SMTP_SERVER="smtp.test.example",
        NOCA_SMTP_USERNAME="u",
        NOCA_SMTP_PASSWORD="p",
    )
    assert ok.SMTP_SERVER == "smtp.test.example"


def test_startup_banner_names_the_delivery_mode_and_relay() -> None:
    from mailer.worker import describe_delivery

    assert describe_delivery(_settings()) == "Mail delivery: MOCK (messages are logged, nothing is sent)"
    assert "MOCK" in describe_delivery(_settings(NOCA_SEND_EMAIL=True, NOCA_EMAIL_PROVIDER="mock"))
    real = _settings(
        NOCA_SEND_EMAIL=True,
        NOCA_EMAIL_PROVIDER="smtp",
        NOCA_SMTP_SERVER="smtp.test.example",
        NOCA_SMTP_PORT=2525,
        NOCA_SMTP_USE_TLS=False,
        NOCA_SMTP_USERNAME="u",
        NOCA_SMTP_PASSWORD="p",
    )
    assert describe_delivery(real) == "Mail delivery: REAL via SMTP smtp.test.example:2525 (no TLS)"
