#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit and live-API tests for shared.services.email_reputation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from shared.services.email_reputation import EmailReputation, EmailReputationService
from shared.services.network_utils import NetworkService, NetworkServiceError

_API_KEY = "test-api-key"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_GOOD_EMAIL = "daniel@lobato.org"
_BAD_EMAIL = "takvaexxaocyufmmoe@jbsze.net"


def _load(name: str) -> dict[str, Any]:
    """Load a sample IPQualityScore response payload from the fixtures dir."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class _FakeNetworkService:
    """Minimal NetworkService stand-in for unit tests."""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        raise_error: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or {}
        self._raise_error = raise_error

    def make_json_request(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append({"url": url, "params": params})
        if self._raise_error:
            raise NetworkServiceError("network down")
        return self._response


# ---------------------------------------------------------------------------
# Disabled service (no API key)
# ---------------------------------------------------------------------------


def test_disabled_when_api_key_is_none() -> None:
    svc = EmailReputationService(None, _FakeNetworkService(_load("good_email.json")))
    assert svc.check(_GOOD_EMAIL) is None


def test_no_network_call_when_api_key_is_none() -> None:
    network = _FakeNetworkService()
    EmailReputationService(None, network).check(_GOOD_EMAIL)
    assert network.calls == []


# ---------------------------------------------------------------------------
# Successful responses (sample payloads)
# ---------------------------------------------------------------------------


def test_good_email_payload_extraction() -> None:
    svc = EmailReputationService(_API_KEY, _FakeNetworkService(_load("good_email.json")))
    result = svc.check(_GOOD_EMAIL)
    assert result == EmailReputation(
        valid=True,
        disposable=False,
        suspect=False,
        overall_score=4,
        common=False,
        fraud_score=0,
        sanitized_email="daniel@lobato.org",
    )


def test_bad_email_payload_extraction() -> None:
    svc = EmailReputationService(_API_KEY, _FakeNetworkService(_load("bad_mail.json")))
    result = svc.check(_BAD_EMAIL)
    assert result == EmailReputation(
        valid=False,
        disposable=True,
        suspect=False,
        overall_score=0,
        common=False,
        fraud_score=100,
        sanitized_email="takvaexxaocyufmmoe@jbsze.net",
    )


def test_request_url_embeds_key_and_encoded_email() -> None:
    network = _FakeNetworkService(_load("good_email.json"))
    EmailReputationService(_API_KEY, network).check("a b+tag@x.com")
    assert len(network.calls) == 1
    url = network.calls[0]["url"]
    assert url == (f"https://www.ipqualityscore.com/api/json/email/{_API_KEY}/a%20b%2Btag%40x.com")
    assert network.calls[0]["params"] == {"timeout": 7}


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_unsuccessful_body_returns_none() -> None:
    svc = EmailReputationService(_API_KEY, _FakeNetworkService({"success": False, "message": "quota"}))
    assert svc.check(_GOOD_EMAIL) is None


def test_network_error_returns_none() -> None:
    svc = EmailReputationService(_API_KEY, _FakeNetworkService(raise_error=True))
    assert svc.check(_GOOD_EMAIL) is None


def test_missing_fields_use_safe_defaults() -> None:
    svc = EmailReputationService(_API_KEY, _FakeNetworkService({"success": True}))
    result = svc.check(_GOOD_EMAIL)
    assert result == EmailReputation(
        valid=False,
        disposable=False,
        suspect=False,
        overall_score=0,
        common=False,
        fraud_score=0,
        sanitized_email=_GOOD_EMAIL,
    )


# ---------------------------------------------------------------------------
# Live API (skipped without a real key)
# ---------------------------------------------------------------------------

_LIVE_KEY = os.environ.get("NOCA_IPQUALITYSCORE_APIKEY")

live = pytest.mark.skipif(not _LIVE_KEY, reason="NOCA_IPQUALITYSCORE_APIKEY not set")


@live
def test_live_good_email() -> None:
    svc = EmailReputationService(_LIVE_KEY, NetworkService())
    result = svc.check(_GOOD_EMAIL)
    assert result is not None
    assert result.valid is True
    assert result.disposable is False
    assert result.sanitized_email


@live
def test_live_bad_email() -> None:
    svc = EmailReputationService(_LIVE_KEY, NetworkService())
    result = svc.check(_BAD_EMAIL)
    assert result is not None
    assert result.disposable is True
