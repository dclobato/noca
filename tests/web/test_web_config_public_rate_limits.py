#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``NOCA_WEB_PUBLIC_RATE_LIMIT_*`` settings: defaults, overrides, and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from web.config import Settings


def _make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extra: str) -> Settings:
    for key in list(os.environ):
        if key.startswith("NOCA_"):
            monkeypatch.delenv(key, raising=False)
    required = {
        "NOCA_DB_USER": "user",
        "NOCA_DB_PASSWORD": "pass",
        "NOCA_DB_SERVER": "localhost",
        "NOCA_DB_NAME": "noca",
        "NOCA_JWT_SECRET_KEY": "secret",
        "NOCA_WEB_PROBLEM_STATEMENT_DIR": str(tmp_path),
        "NOCA_PROBLEM_TESTCASE_DIR": str(tmp_path),
    }
    for key, value in {**required, **extra}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_public_rate_limit_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(monkeypatch, tmp_path)
    assert settings.PUBLIC_RATE_LIMIT_ENABLED is True
    assert settings.PUBLIC_RATE_LIMIT_TRUSTED_CIDRS == "127.0.0.0/8,::1/128"
    assert settings.PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS == 10
    assert settings.PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS == 600
    assert settings.PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS == 120
    assert settings.PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS == 60


def test_public_rate_limit_env_names_resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_WEB_PUBLIC_RATE_LIMIT_ENABLED="false",
        NOCA_WEB_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS=" 10.0.0.0/8 , ::1/128 ",
        NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS="3",
        NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS="30",
        NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS="7",
        NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS="9",
    )
    assert settings.PUBLIC_RATE_LIMIT_ENABLED is False
    assert settings.PUBLIC_RATE_LIMIT_TRUSTED_CIDRS == "10.0.0.0/8,::1/128"
    assert settings.PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS == 3
    assert settings.PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS == 30
    assert settings.PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS == 7
    assert settings.PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS == 9


@pytest.mark.parametrize(
    "name",
    [
        "NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS",
        "NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS",
        "NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS",
        "NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_public_rate_limits_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, **{name: value})


@pytest.mark.parametrize("value", ["", "not-a-cidr", "10.0.0.0/8,garbage"])
def test_public_rate_limit_trusted_cidrs_are_validated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    with pytest.raises(ValidationError, match="NOCA_WEB_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS"):
        _make_settings(monkeypatch, tmp_path, NOCA_WEB_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS=value)


def test_problem_export_rate_limit_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A tenth of the loose read ceiling's rate, over a ten-times-longer window."""
    settings = _make_settings(monkeypatch, tmp_path)
    assert settings.PROBLEM_EXPORT_RATE_LIMIT_ENABLED is True
    assert settings.PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS == 10
    assert settings.PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS == 600


def test_problem_export_rate_limit_reads_its_aliases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_ENABLED="false",
        NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS="3",
        NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS="60",
    )
    assert settings.PROBLEM_EXPORT_RATE_LIMIT_ENABLED is False
    assert settings.PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS == 3
    assert settings.PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS == 60


@pytest.mark.parametrize(
    "variable",
    ["NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS", "NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS"],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_problem_export_rate_limit_rejects_non_positive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str, value: str
) -> None:
    """A zero budget or window would refuse every download rather than none.

    Turning the limit off is what `..._ENABLED=false` is for; a nonsensical
    number must fail at startup instead of silently locking the route.
    """
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, **{variable: value})
