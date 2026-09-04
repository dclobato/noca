#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for health monitor configuration resolution."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from healthmonitor.config import Settings


def _make_settings(monkeypatch: pytest.MonkeyPatch, **extra: str) -> Settings:
    """Build monitor settings from a clean NOCA_ environment plus the given overrides."""
    for key in list(os.environ):
        if key.startswith("NOCA_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """The monitor binds every interface on 8002 unless configured otherwise."""
    settings = _make_settings(monkeypatch)

    assert settings.HOST == "0.0.0.0"
    assert settings.PORT == 8002


def test_env_names_resolve_without_double_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fields bind to NOCA_HEALTHMON_* (no NOCA_NOCA_HEALTHMON_* double prefix)."""
    settings = _make_settings(
        monkeypatch,
        NOCA_HEALTHMON_HOST="127.0.0.1",
        NOCA_HEALTHMON_PORT="9002",
    )

    assert settings.HOST == "127.0.0.1"
    assert settings.PORT == 9002


def test_brand_name_uses_healthmonitor_specific_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public monitor brand resolves from its documented environment variable."""
    settings = _make_settings(monkeypatch, NOCA_HEALTHMON_BRAND_NAME="Contest Operations")

    assert settings.BRAND_NAME == "Contest Operations"


def test_double_prefixed_names_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A doubly prefixed name is not the configured variable and changes nothing."""
    settings = _make_settings(
        monkeypatch,
        NOCA_NOCA_HEALTHMON_HOST="10.0.0.1",
        NOCA_NOCA_HEALTHMON_PORT="9999",
    )

    assert settings.HOST == "0.0.0.0"
    assert settings.PORT == 8002


@pytest.mark.parametrize("port", ["0", "70000", "-1"])
def test_port_range_is_validated(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    """A port outside 1-65535 is refused at startup rather than at bind time."""
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, NOCA_HEALTHMON_PORT=port)


def test_forwarded_allow_ips_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only loopback proxies are trusted until the deployment says otherwise."""
    assert _make_settings(monkeypatch).FORWARDED_ALLOW_IPS == "127.0.0.1,::1"


def test_forwarded_allow_ips_accepts_ips_cidrs_and_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list is normalized the same way the other HTTP runtimes normalize it."""
    settings = _make_settings(monkeypatch, NOCA_FORWARDED_ALLOW_IPS=" 10.0.0.5 , 172.16.0.0/12 ")
    assert settings.FORWARDED_ALLOW_IPS == "10.0.0.5,172.16.0.0/12"
    assert _make_settings(monkeypatch, NOCA_FORWARDED_ALLOW_IPS="*").FORWARDED_ALLOW_IPS == "*"


@pytest.mark.parametrize("value", ["not-an-ip", "", "*,10.0.0.5"])
def test_forwarded_allow_ips_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Same contract the web, arena, and animator modules enforce.

    An unvalidated list would silently change which peers uvicorn rewrites
    ``request.client.host`` for.
    """
    with pytest.raises(ValueError):
        _make_settings(monkeypatch, NOCA_FORWARDED_ALLOW_IPS=value)


def test_rate_limit_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both buckets are on by default with a loopback bypass."""
    settings = _make_settings(monkeypatch)

    assert settings.RATE_LIMIT_ENABLED is True
    assert settings.RATE_LIMIT_MAX_REQUESTS == 120
    assert settings.RATE_LIMIT_WINDOW_SECONDS == 60
    assert settings.RATE_LIMIT_TRUSTED_CIDRS == "127.0.0.0/8,::1/128"
    assert settings.HEALTH_RATE_LIMIT_ENABLED is True
    assert settings.HEALTH_RATE_LIMIT_MAX_REQUESTS == 30
    assert settings.HEALTH_RATE_LIMIT_WINDOW_SECONDS == 60
    assert settings.HEALTH_RATE_LIMIT_TRUSTED_CIDRS == "127.0.0.0/8,::1/128"


def test_rate_limit_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dashboard knobs bind to NOCA_HEALTHMON_RATE_LIMIT_*; health knobs stay unprefixed."""
    settings = _make_settings(
        monkeypatch,
        NOCA_HEALTHMON_RATE_LIMIT_ENABLED="false",
        NOCA_HEALTHMON_RATE_LIMIT_MAX_REQUESTS="7",
        NOCA_HEALTHMON_RATE_LIMIT_WINDOW_SECONDS="9",
        NOCA_HEALTHMON_RATE_LIMIT_TRUSTED_CIDRS=" 10.0.0.0/8 , ,192.0.2.1 ",
        NOCA_HEALTH_RATE_LIMIT_MAX_REQUESTS="5",
        NOCA_NOCA_HEALTHMON_RATE_LIMIT_MAX_REQUESTS="99",
    )

    assert settings.RATE_LIMIT_ENABLED is False
    assert settings.RATE_LIMIT_MAX_REQUESTS == 7
    assert settings.RATE_LIMIT_WINDOW_SECONDS == 9
    assert settings.RATE_LIMIT_TRUSTED_CIDRS == "10.0.0.0/8,192.0.2.1"
    assert settings.HEALTH_RATE_LIMIT_MAX_REQUESTS == 5


@pytest.mark.parametrize(
    "variable",
    [
        "NOCA_HEALTHMON_RATE_LIMIT_MAX_REQUESTS",
        "NOCA_HEALTHMON_RATE_LIMIT_WINDOW_SECONDS",
        "NOCA_HEALTH_RATE_LIMIT_MAX_REQUESTS",
        "NOCA_HEALTH_RATE_LIMIT_WINDOW_SECONDS",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_rate_limit_rejects_non_positive_values(monkeypatch: pytest.MonkeyPatch, variable: str, value: str) -> None:
    """A zero or negative window or budget would disable or invert the limiter silently."""
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, **{variable: value})


@pytest.mark.parametrize(
    "variable",
    ["NOCA_HEALTHMON_RATE_LIMIT_TRUSTED_CIDRS", "NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS"],
)
@pytest.mark.parametrize("value", ["", " , ", "not-a-network", "10.0.0.0/8,bogus"])
def test_rate_limit_rejects_invalid_trusted_cidrs(monkeypatch: pytest.MonkeyPatch, variable: str, value: str) -> None:
    """Both bypass lists refuse empty and malformed entries, naming the variable."""
    with pytest.raises(ValidationError, match=variable):
        _make_settings(monkeypatch, **{variable: value})
