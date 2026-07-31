#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for how health monitor startup configures Uvicorn."""

import pytest

import healthmonitor.main


def test_main_binds_the_configured_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured NOCA_HEALTHMON_HOST/NOCA_HEALTHMON_PORT actually reaches Uvicorn."""
    captured: dict[str, object] = {}

    def _fake_run(*args: object, **kwargs: object) -> None:
        captured["kwargs"] = kwargs

    monkeypatch.setattr(healthmonitor.main.uvicorn, "run", _fake_run)

    healthmonitor.main.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["host"] == healthmonitor.main.settings.HOST
    assert kwargs["port"] == healthmonitor.main.settings.PORT


def test_main_enables_proxy_headers_for_trusted_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forwarded-header support must be limited to the configured trusted proxies.

    Without ``forwarded_allow_ips`` Uvicorn falls back to its own default, so the
    monitor would not honour the same trust boundary as the other HTTP runtimes.
    """
    captured: dict[str, object] = {}

    def _fake_run(*args: object, **kwargs: object) -> None:
        captured["kwargs"] = kwargs

    monkeypatch.setattr(healthmonitor.main.uvicorn, "run", _fake_run)

    healthmonitor.main.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == healthmonitor.main.settings.FORWARDED_ALLOW_IPS
