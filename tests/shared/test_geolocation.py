#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for shared.services.geolocation."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from shared.services.geolocation import GeolocationIP
from shared.services.network_utils import NetworkService, NetworkServiceError

_API_KEY = "test-api-key"
_PUBLIC_IP = "8.8.8.8"
_FULL_LOCATION_RESPONSE: dict[str, Any] = {
    "location": {
        "country_name": "Brazil",
        "state_prov": "São Paulo",
        "district": "Centro",
        "city": "São Paulo",
    }
}


class _FakeNetworkService:
    """Minimal NetworkService stand-in for unit tests."""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        raise_error: bool = False,
        raise_value_error: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or {}
        self._raise_error = raise_error
        self._raise_value_error = raise_value_error

    def make_json_request(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append({"url": url, "params": params})
        if self._raise_error:
            raise NetworkServiceError("network down")
        if self._raise_value_error:
            raise ValueError("bad value")
        return self._response

    @staticmethod
    def is_private_network(ip: str) -> bool:
        return NetworkService.is_private_network(ip)


# ---------------------------------------------------------------------------
# Disabled geolocation (no API key)
# ---------------------------------------------------------------------------


def test_disabled_when_api_key_is_none() -> None:
    svc = GeolocationIP(None, _FakeNetworkService(_FULL_LOCATION_RESPONSE))
    assert svc.get_location_by_ip(_PUBLIC_IP) is None


def test_no_network_call_when_api_key_is_none() -> None:
    network = _FakeNetworkService()
    GeolocationIP(None, network).get_location_by_ip(_PUBLIC_IP)
    assert network.calls == []


# ---------------------------------------------------------------------------
# Private network short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "::1"])
def test_private_ip_returns_none(ip: str) -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(_FULL_LOCATION_RESPONSE))
    assert svc.get_location_by_ip(ip) is None


def test_private_ip_makes_no_network_call() -> None:
    network = _FakeNetworkService(_FULL_LOCATION_RESPONSE)
    GeolocationIP(_API_KEY, network).get_location_by_ip("127.0.0.1")
    assert network.calls == []


# ---------------------------------------------------------------------------
# Successful responses
# ---------------------------------------------------------------------------


def test_full_location_response_formatted_correctly() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(_FULL_LOCATION_RESPONSE))
    result = svc.get_location_by_ip(_PUBLIC_IP)
    assert result == "Brazil, São Paulo, Centro, São Paulo"


def test_partial_location_only_country_and_city() -> None:
    response = {"location": {"country_name": "Brazil", "state_prov": "", "district": "", "city": "Campinas"}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_location_by_ip(_PUBLIC_IP)
    assert result == "Brazil, Campinas"


def test_all_empty_location_fields_return_empty_string() -> None:
    response = {"location": {"country_name": "", "state_prov": "", "district": "", "city": ""}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_location_by_ip(_PUBLIC_IP)
    assert result == ""


def test_missing_location_key_returns_empty_string() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService({}))
    result = svc.get_location_by_ip(_PUBLIC_IP)
    assert result == ""


def test_null_location_value_returns_empty_string() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService({"location": None}))
    result = svc.get_location_by_ip(_PUBLIC_IP)
    assert result == ""


# ---------------------------------------------------------------------------
# API request params
# ---------------------------------------------------------------------------


def test_correct_api_params_passed_to_network_service() -> None:
    network = _FakeNetworkService(_FULL_LOCATION_RESPONSE)
    GeolocationIP(_API_KEY, network).get_location_by_ip(_PUBLIC_IP)
    assert len(network.calls) == 1
    params = network.calls[0]["params"]
    assert params == {"apiKey": _API_KEY, "ip": _PUBLIC_IP}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_network_error_returns_none() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_error=True))
    assert svc.get_location_by_ip(_PUBLIC_IP) is None


def test_value_error_returns_none() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_value_error=True))
    assert svc.get_location_by_ip(_PUBLIC_IP) is None


def test_network_error_is_logged_at_error_level() -> None:
    spy_logger = MagicMock(spec=logging.Logger)
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_error=True), logger=spy_logger)
    svc.get_location_by_ip(_PUBLIC_IP)
    spy_logger.error.assert_called_once()
    call_args = spy_logger.error.call_args
    assert _PUBLIC_IP in call_args[0] or any(_PUBLIC_IP in str(a) for a in call_args[0])


def test_value_error_is_logged_at_error_level() -> None:
    spy_logger = MagicMock(spec=logging.Logger)
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_value_error=True), logger=spy_logger)
    svc.get_location_by_ip(_PUBLIC_IP)
    spy_logger.error.assert_called_once()


def test_custom_logger_is_used() -> None:
    spy_logger = MagicMock(spec=logging.Logger)
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_error=True), logger=spy_logger)
    svc.get_location_by_ip(_PUBLIC_IP)
    # Verify the injected logger was called, not the module-level one.
    spy_logger.error.assert_called()
