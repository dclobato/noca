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
_FULL_RESPONSE: dict[str, Any] = {
    "location": {
        "country_code2": "DE",
        "state_code": "DE-BY",
        "district": "Cham",
        "city": "Falkenstein",
        "is_eu": True,
    },
    "asn": {"as_number": "AS24940"},
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
    svc = GeolocationIP(None, _FakeNetworkService(_FULL_RESPONSE))
    assert svc.get_details_by_ip(_PUBLIC_IP) is None


def test_no_network_call_when_api_key_is_none() -> None:
    network = _FakeNetworkService()
    GeolocationIP(None, network).get_details_by_ip(_PUBLIC_IP)
    assert network.calls == []


# ---------------------------------------------------------------------------
# Private network short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "::1"])
def test_private_ip_returns_none(ip: str) -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(_FULL_RESPONSE))
    assert svc.get_details_by_ip(ip) is None


def test_private_ip_makes_no_network_call() -> None:
    network = _FakeNetworkService(_FULL_RESPONSE)
    GeolocationIP(_API_KEY, network).get_details_by_ip("127.0.0.1")
    assert network.calls == []


# ---------------------------------------------------------------------------
# Successful responses
# ---------------------------------------------------------------------------


def test_full_response_parsed_into_all_fields() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(_FULL_RESPONSE))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.country_code == "DE"
    assert result.subdivision_code == "DE-BY"
    assert result.district == "Cham"
    assert result.city == "Falkenstein"
    assert result.is_eu is True
    assert result.as_number == "AS24940"


def test_missing_location_and_asn_keys_yield_all_none() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService({}))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.country_code is None
    assert result.subdivision_code is None
    assert result.district is None
    assert result.city is None
    assert result.is_eu is None
    assert result.as_number is None


def test_null_location_value_yields_all_none() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService({"location": None, "asn": None}))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.country_code is None
    assert result.as_number is None


# ---------------------------------------------------------------------------
# Type guards and normalization
# ---------------------------------------------------------------------------


def test_country_code_is_uppercased_and_trimmed() -> None:
    response = {"location": {"country_code2": " de "}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.country_code == "DE"


@pytest.mark.parametrize("bad_code", ["DEU", "D", "1E", "", 49])
def test_invalid_country_code_becomes_none(bad_code: Any) -> None:
    response = {"location": {"country_code2": bad_code, "state_code": "DE-BY"}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.country_code is None
    # subdivision requires a valid country prefix, so it is discarded too.
    assert result.subdivision_code is None


def test_subdivision_without_country_prefix_is_discarded() -> None:
    response = {"location": {"country_code2": "DE", "state_code": "FR-75"}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.country_code == "DE"
    assert result.subdivision_code is None


def test_bare_subdivision_name_is_discarded() -> None:
    response = {"location": {"country_code2": "DE", "state_code": "Bavaria"}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.subdivision_code is None


@pytest.mark.parametrize("bad_is_eu", [1, 0, "true", "false", None])
def test_non_bool_is_eu_becomes_none(bad_is_eu: Any) -> None:
    response = {"location": {"country_code2": "DE", "is_eu": bad_is_eu}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.is_eu is None


def test_is_eu_false_is_preserved() -> None:
    response = {"location": {"country_code2": "US", "is_eu": False}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.is_eu is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("AS24940", "AS24940"), ("24940", "24940"), (24940, "24940"), ("  AS7 ", "AS7"), (True, None), ({}, None)],
)
def test_as_number_normalization(raw: Any, expected: str | None) -> None:
    response = {"asn": {"as_number": raw}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.as_number == expected


def test_oversized_subdivision_is_discarded() -> None:
    # Prefix matches but the value exceeds the String(16) column bound.
    response = {"location": {"country_code2": "DE", "state_code": "DE-" + "X" * 20}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.subdivision_code is None


def test_oversized_district_and_city_are_discarded() -> None:
    response = {"location": {"country_code2": "DE", "district": "D" * 200, "city": "C" * 129}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.district is None
    assert result.city is None


@pytest.mark.parametrize("raw", ["AS" + "9" * 20, 10**20])
def test_oversized_as_number_is_discarded(raw: Any) -> None:
    response = {"asn": {"as_number": raw}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.as_number is None


def test_district_and_city_at_max_length_are_kept() -> None:
    response = {"location": {"country_code2": "DE", "district": "D" * 128, "city": "C" * 128}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.district == "D" * 128
    assert result.city == "C" * 128


def test_empty_district_and_city_become_none() -> None:
    response = {"location": {"country_code2": "DE", "district": "  ", "city": ""}}
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(response))
    result = svc.get_details_by_ip(_PUBLIC_IP)
    assert result is not None
    assert result.district is None
    assert result.city is None


# ---------------------------------------------------------------------------
# API request params
# ---------------------------------------------------------------------------


def test_correct_api_params_passed_to_network_service() -> None:
    network = _FakeNetworkService(_FULL_RESPONSE)
    GeolocationIP(_API_KEY, network).get_details_by_ip(_PUBLIC_IP)
    assert len(network.calls) == 1
    params = network.calls[0]["params"]
    assert params == {"apiKey": _API_KEY, "ip": _PUBLIC_IP}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_network_error_returns_none() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_error=True))
    assert svc.get_details_by_ip(_PUBLIC_IP) is None


def test_value_error_returns_none() -> None:
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_value_error=True))
    assert svc.get_details_by_ip(_PUBLIC_IP) is None


def test_network_error_is_logged_at_error_level() -> None:
    spy_logger = MagicMock(spec=logging.Logger)
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_error=True), logger=spy_logger)
    svc.get_details_by_ip(_PUBLIC_IP)
    spy_logger.error.assert_called_once()
    call_args = spy_logger.error.call_args
    assert _PUBLIC_IP in call_args[0] or any(_PUBLIC_IP in str(a) for a in call_args[0])


def test_value_error_is_logged_at_error_level() -> None:
    spy_logger = MagicMock(spec=logging.Logger)
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_value_error=True), logger=spy_logger)
    svc.get_details_by_ip(_PUBLIC_IP)
    spy_logger.error.assert_called_once()


def test_custom_logger_is_used() -> None:
    spy_logger = MagicMock(spec=logging.Logger)
    svc = GeolocationIP(_API_KEY, _FakeNetworkService(raise_error=True), logger=spy_logger)
    svc.get_details_by_ip(_PUBLIC_IP)
    # Verify the injected logger was called, not the module-level one.
    spy_logger.error.assert_called()
