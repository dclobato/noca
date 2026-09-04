#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``NOCA_ANIMATOR_SSE_*`` settings: defaults, environment overrides, and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from animator.config import Settings


def _make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extra: str) -> Settings:
    for key in list(os.environ):
        if key.startswith("NOCA_"):
            monkeypatch.delenv(key, raising=False)
    required = {
        "NOCA_DB_USER": "user",
        "NOCA_DB_PASSWORD": "pass",
        "NOCA_DB_SERVER": "localhost",
        "NOCA_DB_NAME": "noca",
    }
    for key, value in {**required, **extra}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_sse_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(monkeypatch, tmp_path)
    assert settings.SSE_LIMIT_ENABLED is True
    assert settings.SSE_MAX_PER_IP == 100
    assert settings.SSE_CONNECTION_TTL_SECONDS == 600
    assert settings.SSE_TRUSTED_CIDRS == "127.0.0.0/8,::1/128"
    assert settings.MAX_SSE_CLIENTS == 2000


def test_sse_env_names_resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        NOCA_ANIMATOR_SSE_LIMIT_ENABLED="false",
        NOCA_ANIMATOR_SSE_MAX_PER_IP="3",
        NOCA_ANIMATOR_SSE_CONNECTION_TTL_SECONDS="120",
        NOCA_ANIMATOR_SSE_TRUSTED_CIDRS=" 10.0.0.0/8 , ::1/128 ",
        NOCA_ANIMATOR_MAX_SSE_CLIENTS="50",
    )
    assert settings.SSE_LIMIT_ENABLED is False
    assert settings.SSE_MAX_PER_IP == 3
    assert settings.SSE_CONNECTION_TTL_SECONDS == 120
    assert settings.SSE_TRUSTED_CIDRS == "10.0.0.0/8,::1/128"
    assert settings.MAX_SSE_CLIENTS == 50


@pytest.mark.parametrize(
    "name",
    ["NOCA_ANIMATOR_SSE_MAX_PER_IP", "NOCA_ANIMATOR_SSE_CONNECTION_TTL_SECONDS", "NOCA_ANIMATOR_MAX_SSE_CLIENTS"],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_sse_limits_must_be_positive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, tmp_path, **{name: value})


@pytest.mark.parametrize("value", ["", "not-a-cidr", "10.0.0.0/8,garbage"])
def test_sse_trusted_cidrs_are_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str) -> None:
    with pytest.raises(ValidationError, match="NOCA_ANIMATOR_SSE_TRUSTED_CIDRS"):
        _make_settings(monkeypatch, tmp_path, NOCA_ANIMATOR_SSE_TRUSTED_CIDRS=value)
