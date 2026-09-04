#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Team write-throttle settings: defaults, overrides, and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from web.config import Settings


def _make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extra: str) -> Settings:
    """Build a Settings instance from a clean environment.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Directory used for the required path settings.
        **extra: Environment variables under test.

    Returns:
        The constructed settings object.
    """
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


def test_team_write_limit_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(monkeypatch, tmp_path)
    assert settings.TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS == 600
    assert settings.TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS == 5
    assert settings.TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS == 3
    assert settings.TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS == 10
    assert settings.CLARIFICATION_RATE_LIMIT_WINDOW_SECONDS == 600
    assert settings.CLARIFICATION_RATE_LIMIT_MAX_REQUESTS == 5
    assert settings.CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED == 3


def test_team_write_limit_env_names_resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_WEB_TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS="120",
        NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS="2",
        NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS="1",
        NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS="4",
        NOCA_WEB_CLARIFICATION_RATE_LIMIT_WINDOW_SECONDS="90",
        NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_REQUESTS="6",
        NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED="2",
    )

    assert settings.TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS == 120
    assert settings.TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS == 2
    assert settings.TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS == 1
    assert settings.TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS == 4
    assert settings.CLARIFICATION_RATE_LIMIT_WINDOW_SECONDS == 90
    assert settings.CLARIFICATION_RATE_LIMIT_MAX_REQUESTS == 6
    assert settings.CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED == 2


@pytest.mark.parametrize(
    "variable",
    [
        "NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS",
        "NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS",
        "NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS",
        "NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_REQUESTS",
        "NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED",
    ],
)
def test_zero_disables_a_rule_but_negative_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str
) -> None:
    """Zero is the documented "unlimited" escape hatch; a negative value is a typo."""
    settings = _make_settings(monkeypatch, tmp_path, **{variable: "0"})
    assert getattr(settings, variable.removeprefix("NOCA_WEB_")) == 0

    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, **{variable: "-1"})


@pytest.mark.parametrize(
    "variable",
    [
        "NOCA_WEB_TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS",
        "NOCA_WEB_CLARIFICATION_RATE_LIMIT_WINDOW_SECONDS",
    ],
)
def test_windows_must_be_positive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str) -> None:
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, **{variable: "0"})
