#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the web server's bind address and port configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from web.config import Settings


def _make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extra: str) -> Settings:
    """Build web settings from a clean NOCA_ environment plus the given overrides."""
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


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The web server binds every interface on 8000 unless configured otherwise."""
    settings = _make_settings(monkeypatch, tmp_path)

    assert settings.HOST == "0.0.0.0"
    assert settings.PORT == 8000


def test_env_names_resolve_without_double_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The fields bind to NOCA_WEB_* (no NOCA_NOCA_WEB_* double prefix)."""
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_WEB_HOST="127.0.0.1",
        NOCA_WEB_PORT="9000",
    )

    assert settings.HOST == "127.0.0.1"
    assert settings.PORT == 9000


def test_double_prefixed_names_are_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A doubly prefixed name is not the configured variable and changes nothing."""
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_NOCA_WEB_HOST="10.0.0.1",
        NOCA_NOCA_WEB_PORT="9999",
    )

    assert settings.HOST == "0.0.0.0"
    assert settings.PORT == 8000


@pytest.mark.parametrize("port", ["0", "70000", "-1"])
def test_port_range_is_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, port: str) -> None:
    """A port outside 1-65535 is refused at startup rather than at bind time."""
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, NOCA_WEB_PORT=port)
