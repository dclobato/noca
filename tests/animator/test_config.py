#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for animator configuration resolution."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from animator.config import Settings
from shared.enumerations import Environment

_BASE_DB_ENV = {
    "NOCA_DB_USER": "user",
    "NOCA_DB_PASSWORD": "pass",
    "NOCA_DB_SERVER": "localhost",
    "NOCA_DB_NAME": "noca",
}


def _clear_animator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove any inherited NOCA_ variables so defaults are deterministic."""
    for key in list(__import__("os").environ):
        if key.startswith("NOCA_"):
            monkeypatch.delenv(key, raising=False)


def _make_settings(monkeypatch: pytest.MonkeyPatch, **extra: str) -> Settings:
    _clear_animator_env(monkeypatch)
    for key, value in {**_BASE_DB_ENV, **extra}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Animator-specific settings fall back to documented defaults."""
    settings = _make_settings(monkeypatch)

    assert settings.HOST == "0.0.0.0"
    assert settings.PORT == 8003
    assert settings.POLL_FALLBACK_SECONDS == 15
    assert settings.ENABLE_CONTROL is False
    assert settings.CONTROLLER_LEASE_TTL_SECONDS == 45
    assert settings.CONTROLLER_HEARTBEAT_SECONDS == 10
    assert settings.BRAND_NAME == "NOCA Animator"
    assert settings.HEALTHMON_URL == ""
    assert settings.STARTUP_TIMEOUT_SECONDS == 60
    assert settings.ENVIRONMENT == Environment.DEVELOPMENT


def test_animator_env_names_resolve_without_double_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Animator vars bind to NOCA_ANIMATOR_* (no NOCA_NOCA_ANIMATOR_* double prefix)."""
    settings = _make_settings(
        monkeypatch,
        NOCA_ANIMATOR_HOST="127.0.0.1",
        NOCA_ANIMATOR_PORT="9100",
        NOCA_ANIMATOR_POLL_FALLBACK_SECONDS="30",
        NOCA_ANIMATOR_ENABLE_CONTROL="true",
        NOCA_ANIMATOR_BRAND_NAME="Contest X",
        NOCA_HEALTHMON_URL="https://status.example.test",
    )

    assert settings.HOST == "127.0.0.1"
    assert settings.PORT == 9100
    assert settings.POLL_FALLBACK_SECONDS == 30
    assert settings.ENABLE_CONTROL is True
    assert settings.BRAND_NAME == "Contest X"
    assert settings.HEALTHMON_URL == "https://status.example.test"


def test_double_prefixed_names_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A NOCA_NOCA_ANIMATOR_* variable must not be picked up."""
    settings = _make_settings(monkeypatch, NOCA_NOCA_ANIMATOR_PORT="9999")

    assert settings.PORT == 8003


def test_port_range_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """An out-of-range port is rejected."""
    with pytest.raises(ValueError):
        _make_settings(monkeypatch, NOCA_ANIMATOR_PORT="70000")


def test_poll_fallback_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """A poll-fallback below one second is rejected."""
    with pytest.raises(ValueError):
        _make_settings(monkeypatch, NOCA_ANIMATOR_POLL_FALLBACK_SECONDS="0")


def test_controller_lease_env_names_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controller timings bind to their explicit animator environment names."""
    settings = _make_settings(
        monkeypatch,
        NOCA_ANIMATOR_CONTROLLER_LEASE_TTL_SECONDS="90",
        NOCA_ANIMATOR_CONTROLLER_HEARTBEAT_SECONDS="25",
    )

    assert settings.CONTROLLER_LEASE_TTL_SECONDS == 90
    assert settings.CONTROLLER_HEARTBEAT_SECONDS == 25


@pytest.mark.parametrize(("ttl", "heartbeat"), [("29", "10"), ("30", "11")])
def test_controller_lease_ttl_must_cover_three_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
    ttl: str,
    heartbeat: str,
) -> None:
    """A lease that cannot survive two missed heartbeats is rejected."""
    with pytest.raises(ValueError, match="at least 3 times"):
        _make_settings(
            monkeypatch,
            NOCA_ANIMATOR_CONTROLLER_LEASE_TTL_SECONDS=ttl,
            NOCA_ANIMATOR_CONTROLLER_HEARTBEAT_SECONDS=heartbeat,
        )


def test_urls_and_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derived URLs and the resolved log level reflect configuration."""
    settings = _make_settings(
        monkeypatch,
        NOCA_VALKEY_SERVER="valkey",
        NOCA_VALKEY_PORT="6380",
        NOCA_LOG_LEVEL="warning",
        NOCA_ENVIRONMENT="production",
    )

    assert settings.db_url == "postgresql+asyncpg://user:pass@localhost:5432/noca"
    assert settings.valkey_url == "redis://valkey:6380/0"
    assert settings.resolved_log_level == logging.WARNING


def test_forwarded_allow_ips_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _make_settings(monkeypatch).FORWARDED_ALLOW_IPS == "127.0.0.1,::1"


def test_forwarded_allow_ips_accepts_ips_cidrs_and_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_settings(monkeypatch, NOCA_FORWARDED_ALLOW_IPS=" 10.0.0.5 , 172.16.0.0/12 ")
    assert settings.FORWARDED_ALLOW_IPS == "10.0.0.5,172.16.0.0/12"
    assert _make_settings(monkeypatch, NOCA_FORWARDED_ALLOW_IPS="*").FORWARDED_ALLOW_IPS == "*"


@pytest.mark.parametrize("value", ["not-an-ip", "", "*,10.0.0.5"])
def test_forwarded_allow_ips_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Same contract the web and arena modules enforce.

    An unvalidated list would silently disable the proxy rewrite uvicorn does on
    ``request.client.host``, and the control audit would then record the reverse
    proxy instead of the operator.
    """
    with pytest.raises(ValueError):
        _make_settings(monkeypatch, NOCA_FORWARDED_ALLOW_IPS=value)


def test_worker_presence_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Presence settings fall back to the documented defaults."""
    settings = _make_settings(monkeypatch)

    assert settings.WORKER_ID == ""
    assert settings.WORKER_PRESENCE_INTERVAL_SECONDS == 30.0
    assert settings.WORKER_PRESENCE_TTL_SECONDS == 60


def test_worker_presence_env_names_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Presence vars bind to NOCA_ANIMATOR_* without a double prefix."""
    settings = _make_settings(
        monkeypatch,
        NOCA_ANIMATOR_WORKER_ID="projector-1",
        NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS="5",
        NOCA_ANIMATOR_WORKER_PRESENCE_TTL_SECONDS="20",
    )

    assert settings.WORKER_ID == "projector-1"
    assert settings.WORKER_PRESENCE_INTERVAL_SECONDS == 5.0
    assert settings.WORKER_PRESENCE_TTL_SECONDS == 20


def test_worker_presence_ttl_must_exceed_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TTL at or below the interval is refused.

    A marker that expires before the next heartbeat would flap the animator
    between Available and Unavailable on the health monitor.
    """
    with pytest.raises(ValueError, match="must be greater than"):
        _make_settings(
            monkeypatch,
            NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS="30",
            NOCA_ANIMATOR_WORKER_PRESENCE_TTL_SECONDS="30",
        )


@pytest.mark.parametrize(
    ("interval", "ttl"),
    [("0.5", "60"), ("301", "600"), ("30", "1"), ("30", "3601")],
)
def test_worker_presence_ranges_are_validated(monkeypatch: pytest.MonkeyPatch, interval: str, ttl: str) -> None:
    """Out-of-range presence timings are refused."""
    with pytest.raises(ValueError):
        _make_settings(
            monkeypatch,
            NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS=interval,
            NOCA_ANIMATOR_WORKER_PRESENCE_TTL_SECONDS=ttl,
        )


def test_feed_cache_and_public_rate_limit_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_settings(monkeypatch)

    assert settings.SNAPSHOT_CACHE_SECONDS == 5
    assert settings.SNAPSHOT_CACHE_ENDED_SECONDS == 60
    assert settings.META_CACHE_SECONDS == 30
    assert settings.REVEAL_DATASET_CACHE_SECONDS == 300
    assert settings.PUBLIC_RATE_LIMIT_ENABLED is True
    assert settings.PUBLIC_RATE_LIMIT_MAX_REQUESTS == 300
    assert settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS == 60
    assert settings.PUBLIC_RATE_LIMIT_TRUSTED_CIDRS == "127.0.0.0/8,::1/128"


def test_feed_cache_and_public_rate_limit_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_settings(
        monkeypatch,
        NOCA_ANIMATOR_SNAPSHOT_CACHE_SECONDS="2",
        NOCA_ANIMATOR_SNAPSHOT_CACHE_ENDED_SECONDS="120",
        NOCA_ANIMATOR_META_CACHE_SECONDS="10",
        NOCA_ANIMATOR_REVEAL_DATASET_CACHE_SECONDS="600",
        NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_ENABLED="false",
        NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_MAX_REQUESTS="50",
        NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_WINDOW_SECONDS="30",
        NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS=" 10.0.0.0/8 , ::1/128 ",
    )

    assert settings.SNAPSHOT_CACHE_SECONDS == 2
    assert settings.SNAPSHOT_CACHE_ENDED_SECONDS == 120
    assert settings.META_CACHE_SECONDS == 10
    assert settings.REVEAL_DATASET_CACHE_SECONDS == 600
    assert settings.PUBLIC_RATE_LIMIT_ENABLED is False
    assert settings.PUBLIC_RATE_LIMIT_MAX_REQUESTS == 50
    assert settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS == 30
    assert settings.PUBLIC_RATE_LIMIT_TRUSTED_CIDRS == "10.0.0.0/8,::1/128"


def test_public_rate_limit_trusted_cidrs_are_validated_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS"):
        _make_settings(monkeypatch, NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS="not-a-cidr")
    with pytest.raises(ValueError, match="NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS"):
        _make_settings(monkeypatch, NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS="not-a-cidr")


def test_control_lockout_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_settings(monkeypatch)
    assert settings.CONTROL_LOCKOUT_ENABLED is True
    assert settings.CONTROL_LOCKOUT_FAILURES == 10
    assert settings.CONTROL_LOCKOUT_SECONDS == 300


def test_control_lockout_env_names_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_settings(
        monkeypatch,
        NOCA_ANIMATOR_CONTROL_LOCKOUT_ENABLED="false",
        NOCA_ANIMATOR_CONTROL_LOCKOUT_FAILURES="3",
        NOCA_ANIMATOR_CONTROL_LOCKOUT_SECONDS="60",
    )
    assert settings.CONTROL_LOCKOUT_ENABLED is False
    assert settings.CONTROL_LOCKOUT_FAILURES == 3
    assert settings.CONTROL_LOCKOUT_SECONDS == 60


@pytest.mark.parametrize("name", ["NOCA_ANIMATOR_CONTROL_LOCKOUT_FAILURES", "NOCA_ANIMATOR_CONTROL_LOCKOUT_SECONDS"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_control_lockout_values_must_be_positive(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _make_settings(monkeypatch, **{name: value})
