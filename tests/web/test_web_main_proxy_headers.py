#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for how web startup configures Uvicorn."""

import pytest

import web.main


def _capture_uvicorn_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Run ``main()`` with a stubbed Uvicorn and return the keyword arguments."""
    captured: dict[str, object] = {}

    def _fake_run(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(web.main.uvicorn, "run", _fake_run)

    web.main.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    return kwargs


def test_main_enables_proxy_headers_for_trusted_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must start Uvicorn with forwarded-header support enabled."""
    kwargs = _capture_uvicorn_kwargs(monkeypatch)

    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == web.main.settings.FORWARDED_ALLOW_IPS


def test_main_binds_the_configured_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured NOCA_WEB_HOST/NOCA_WEB_PORT actually reaches Uvicorn."""
    kwargs = _capture_uvicorn_kwargs(monkeypatch)

    assert kwargs["host"] == web.main.settings.HOST
    assert kwargs["port"] == web.main.settings.PORT
