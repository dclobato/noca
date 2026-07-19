#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared.services.network_utils.ip_reputation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from dotenv import load_dotenv

from shared.services.network_utils import IPQualityScoreIPReputationService, IPReputation
from shared.services.network_utils.errors import NetworkServiceError
from shared.services.network_utils.service import NetworkService

load_dotenv(override=False)

_API_KEY = "test-api-key"
_GOOD_IP = "187.75.20.15"
_BAD_IP = "137.131.144.150"
_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


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
        """Record the call and return the configured response."""
        self.calls.append({"url": url, "params": params})
        if self._raise_error:
            raise NetworkServiceError("network down")
        if self._raise_value_error:
            raise ValueError("bad value")
        return self._response


def _load_fixture(name: str) -> dict[str, Any]:
    """Load an IPQualityScore fixture by filename."""
    with (_FIXTURE_DIR / name).open(encoding="utf-8") as fixture_file:
        data = json.load(fixture_file)
    assert isinstance(data, dict)
    return data


def _service(response: dict[str, Any]) -> tuple[IPQualityScoreIPReputationService, _FakeNetworkService]:
    """Build a service backed by a fake network implementation."""
    network = _FakeNetworkService(response)
    return IPQualityScoreIPReputationService(_API_KEY, cast(NetworkService, network)), network


def test_disabled_when_api_key_is_none() -> None:
    network = _FakeNetworkService(_load_fixture("ipqualityscore_good_ip.json"))
    service = IPQualityScoreIPReputationService(None, cast(NetworkService, network))

    assert service.check(_GOOD_IP) is None
    assert network.calls == []


@pytest.mark.parametrize("ip_address", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "::1"])
def test_private_ip_returns_none_without_network_call(ip_address: str) -> None:
    service, network = _service(_load_fixture("ipqualityscore_good_ip.json"))

    assert service.check(ip_address) is None
    assert network.calls == []


def test_good_fixture_parses_requested_fields() -> None:
    service, network = _service(_load_fixture("ipqualityscore_good_ip.json"))

    result = service.check(_GOOD_IP)

    assert result == IPReputation(
        is_crawler=False,
        mobile=False,
        recent_abuse=False,
        fraud_score=0,
        proxy=False,
        vpn=False,
        tor=False,
        active_vpn=False,
        active_tor=False,
    )
    assert network.calls == [
        {
            "url": f"https://www.ipqualityscore.com/api/json/ip/{_API_KEY}/{_GOOD_IP}",
            "params": {"strictness": 1, "allow_public_access_points": "true"},
        }
    ]


def test_bad_fixture_parses_requested_fields() -> None:
    service, _network = _service(_load_fixture("ipqualityscore_bad_ip.json"))

    result = service.check(_BAD_IP)

    assert result == IPReputation(
        is_crawler=False,
        mobile=False,
        recent_abuse=False,
        fraud_score=75,
        proxy=True,
        vpn=True,
        tor=False,
        active_vpn=False,
        active_tor=False,
    )


@pytest.mark.parametrize(
    "response",
    [
        {"success": False, "message": "invalid IP"},
        {"message": "missing success"},
    ],
)
def test_unsuccessful_response_returns_none(response: dict[str, Any]) -> None:
    service, _network = _service(response)

    assert service.check(_GOOD_IP) is None


@pytest.mark.parametrize(
    "network",
    [
        _FakeNetworkService(raise_error=True),
        _FakeNetworkService(raise_value_error=True),
    ],
)
def test_network_errors_return_none(network: _FakeNetworkService) -> None:
    service = IPQualityScoreIPReputationService(_API_KEY, cast(NetworkService, network))

    assert service.check(_GOOD_IP) is None


def test_wrong_json_types_default_to_safe_values() -> None:
    service, _network = _service(
        {
            "success": True,
            "is_crawler": "false",
            "mobile": 1,
            "recent_abuse": None,
            "fraud_score": True,
            "proxy": "true",
            "vpn": 0,
            "tor": [],
            "active_vpn": {},
            "active_tor": "false",
        }
    )

    assert service.check(_GOOD_IP) == IPReputation(
        is_crawler=False,
        mobile=False,
        recent_abuse=False,
        fraud_score=0,
        proxy=False,
        vpn=False,
        tor=False,
        active_vpn=False,
        active_tor=False,
    )


@pytest.mark.real_ipqualityscore
@pytest.mark.parametrize(
    ("ip_address", "fixture_name"),
    [
        (_GOOD_IP, "ipqualityscore_good_ip.json"),
        (_BAD_IP, "ipqualityscore_bad_ip.json"),
    ],
)
def test_real_ipqualityscore_lookup_matches_sample(ip_address: str, fixture_name: str) -> None:
    api_key = os.getenv("NOCA_IPQUALITYSCORE_APIKEY")
    if not api_key:
        pytest.skip("Set NOCA_IPQUALITYSCORE_APIKEY in the environment or .env to run real IPQS tests")

    expected_service, _network = _service(_load_fixture(fixture_name))
    expected = expected_service.check(ip_address)
    assert expected is not None

    result = IPQualityScoreIPReputationService(api_key, NetworkService()).check(ip_address)

    assert result == expected
