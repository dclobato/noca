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


def test_ranking_medal_cutoffs_default_to_one_two_three(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Out of the box the podium keeps the historical 1/2/3 bands."""
    settings = _make_settings(monkeypatch, tmp_path)

    assert settings.ARENA_RANKING_MEDAL_GOLD_CUTOFF == 1
    assert settings.ARENA_RANKING_MEDAL_SILVER_CUTOFF == 2
    assert settings.ARENA_RANKING_MEDAL_BRONZE_CUTOFF == 3


def test_ranking_medal_cutoffs_read_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The three NOCA_ARENA_RANKING_MEDAL_* variables widen the bands."""
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_ARENA_RANKING_MEDAL_GOLD_CUTOFF="3",
        NOCA_ARENA_RANKING_MEDAL_SILVER_CUTOFF="10",
        NOCA_ARENA_RANKING_MEDAL_BRONZE_CUTOFF="25",
    )

    assert settings.ARENA_RANKING_MEDAL_GOLD_CUTOFF == 3
    assert settings.ARENA_RANKING_MEDAL_SILVER_CUTOFF == 10
    assert settings.ARENA_RANKING_MEDAL_BRONZE_CUTOFF == 25


@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [("0", "2", "3"), ("1", "0", "3"), ("1", "2", "0"), ("0", "0", "0"), ("2", "2", "5")],
)
def test_ranking_medal_cutoffs_accept_disabled_and_equal_bands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gold: str, silver: str, bronze: str
) -> None:
    """A 0 disables its band and is skipped by the ordering rule; equal cutoffs are legal."""
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_ARENA_RANKING_MEDAL_GOLD_CUTOFF=gold,
        NOCA_ARENA_RANKING_MEDAL_SILVER_CUTOFF=silver,
        NOCA_ARENA_RANKING_MEDAL_BRONZE_CUTOFF=bronze,
    )

    assert int(gold) == settings.ARENA_RANKING_MEDAL_GOLD_CUTOFF


@pytest.mark.parametrize(("gold", "silver", "bronze"), [("3", "2", "1"), ("5", "2", "10"), ("0", "5", "2")])
def test_ranking_medal_cutoffs_reject_decreasing_bands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gold: str, silver: str, bronze: str
) -> None:
    """Enabled cutoffs must not decrease, and the error names the env vars."""
    with pytest.raises(ValidationError, match="NOCA_ARENA_RANKING_MEDAL_GOLD_CUTOFF"):
        _make_settings(
            monkeypatch,
            tmp_path,
            NOCA_ARENA_RANKING_MEDAL_GOLD_CUTOFF=gold,
            NOCA_ARENA_RANKING_MEDAL_SILVER_CUTOFF=silver,
            NOCA_ARENA_RANKING_MEDAL_BRONZE_CUTOFF=bronze,
        )


@pytest.mark.parametrize("value", ["-1", "1001"])
def test_ranking_medal_cutoffs_are_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str) -> None:
    """Cutoffs outside 0..1000 are rejected."""
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, NOCA_ARENA_RANKING_MEDAL_GOLD_CUTOFF=value)


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


def test_signup_rate_limit_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(monkeypatch, tmp_path)
    assert settings.SIGNUP_RATE_LIMIT_MAX_REQUESTS == 5
    assert settings.SIGNUP_RATE_LIMIT_WINDOW_SECONDS == 3600


def test_signup_rate_limit_reads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_ARENA_SIGNUP_RATE_LIMIT_MAX_REQUESTS="12",
        NOCA_ARENA_SIGNUP_RATE_LIMIT_WINDOW_SECONDS="120",
    )
    assert settings.SIGNUP_RATE_LIMIT_MAX_REQUESTS == 12
    assert settings.SIGNUP_RATE_LIMIT_WINDOW_SECONDS == 120


@pytest.mark.parametrize(
    "variable",
    ["NOCA_ARENA_SIGNUP_RATE_LIMIT_MAX_REQUESTS", "NOCA_ARENA_SIGNUP_RATE_LIMIT_WINDOW_SECONDS"],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_signup_rate_limit_rejects_non_positive_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, **{variable: value})
