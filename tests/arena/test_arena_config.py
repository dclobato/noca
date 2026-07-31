#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena environment configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from arena.config import Settings
from shared.services.imageprocessing_service import MAX_IMAGE_FILE_SIZE


def _make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extra: str) -> Settings:
    """Build arena settings from a clean NOCA_ environment plus the given overrides."""
    for key in list(os.environ):
        if key.startswith("NOCA_"):
            monkeypatch.delenv(key, raising=False)
    required = {
        "NOCA_DB_USER": "user",
        "NOCA_DB_PASSWORD": "pass",
        "NOCA_DB_SERVER": "localhost",
        "NOCA_DB_NAME": "noca",
        "NOCA_JWT_SECRET_KEY": "secret",
        "NOCA_PROBLEM_TESTCASE_DIR": str(tmp_path),
    }
    for key, value in {**required, **extra}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_host_and_port_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Arena binds every interface on 8001 unless configured otherwise."""
    settings = _make_settings(monkeypatch, tmp_path)

    assert settings.HOST == "0.0.0.0"
    assert settings.PORT == 8001


def test_host_and_port_resolve_without_double_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The fields bind to NOCA_ARENA_* (no NOCA_NOCA_ARENA_* double prefix)."""
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_ARENA_HOST="127.0.0.1",
        NOCA_ARENA_PORT="9001",
    )

    assert settings.HOST == "127.0.0.1"
    assert settings.PORT == 9001


def test_double_prefixed_host_and_port_are_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A doubly prefixed name is not the configured variable and changes nothing."""
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_NOCA_ARENA_HOST="10.0.0.1",
        NOCA_NOCA_ARENA_PORT="9999",
    )

    assert settings.HOST == "0.0.0.0"
    assert settings.PORT == 8001


@pytest.mark.parametrize("port", ["0", "70000", "-1"])
def test_port_range_is_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, port: str) -> None:
    """A port outside 1-65535 is refused at startup rather than at bind time."""
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, NOCA_ARENA_PORT=port)


def test_image_max_file_size_defaults_to_two_mebibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arena defaults image uploads below the shared hard limit."""
    monkeypatch.delenv("NOCA_IMAGE_MAX_FILE_SIZE", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.IMAGE_MAX_FILE_SIZE == 2 * 1024 * 1024


def test_image_max_file_size_rejects_values_above_shared_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arena rejects image upload settings above the shared hard limit."""
    monkeypatch.setenv("NOCA_IMAGE_MAX_FILE_SIZE", str(MAX_IMAGE_FILE_SIZE + 1))

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


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
