#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena environment configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from arena.config import Settings


def test_arena_live_feed_limit_defaults_to_twenty(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public live feed returns 20 rows unless configured otherwise."""
    monkeypatch.delenv("NOCA_ARENA_LIVE_FEED_LIMIT", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.ARENA_LIVE_FEED_LIMIT == 20


def test_arena_live_feed_limit_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOCA_ARENA_LIVE_FEED_LIMIT overrides the default row count."""
    monkeypatch.setenv("NOCA_ARENA_LIVE_FEED_LIMIT", "42")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.ARENA_LIVE_FEED_LIMIT == 42


def test_arena_live_feed_limit_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured live feed row count must stay within the supported range."""
    monkeypatch.setenv("NOCA_ARENA_LIVE_FEED_LIMIT", "101")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_health_rate_limit_trusted_cidrs_accepts_valid_cidrs() -> None:
    """Health rate-limit trusted networks are normalized."""
    value = Settings.normalize_health_rate_limit_trusted_cidrs(" 127.0.0.0/8 , ::1/128 ")
    assert value == "127.0.0.0/8,::1/128"


def test_health_rate_limit_trusted_cidrs_rejects_invalid_token() -> None:
    """Invalid health rate-limit trusted networks are rejected."""
    with pytest.raises(ValueError, match="valid CIDRs"):
        Settings.normalize_health_rate_limit_trusted_cidrs("127.0.0.0/8,not-a-cidr")


def test_health_rate_limit_trusted_cidrs_rejects_empty_value() -> None:
    """At least one trusted health CIDR must be configured."""
    with pytest.raises(ValueError, match="cannot be empty"):
        Settings.normalize_health_rate_limit_trusted_cidrs(" , ")


@pytest.mark.parametrize("value", [None, "", "   ", "\t"])
def test_mbox_log_dir_empty_or_blank_is_disabled(value: str | None) -> None:
    """Empty or whitespace-only values disable the mbox audit log."""
    assert Settings.normalize_mbox_log_dir(value) is None


def test_mbox_log_dir_accepts_absolute_path() -> None:
    """An absolute path is accepted as-is."""
    assert Settings.normalize_mbox_log_dir("/var/log/noca/email") == "/var/log/noca/email"


def test_mbox_log_dir_strips_padded_absolute_path() -> None:
    """Surrounding whitespace is stripped before validation."""
    assert Settings.normalize_mbox_log_dir("  /var/log/noca/email  ") == "/var/log/noca/email"


def test_mbox_log_dir_rejects_relative_path() -> None:
    """A relative path is rejected."""
    with pytest.raises(ValueError, match="absolute path"):
        Settings.normalize_mbox_log_dir("relative/path")
