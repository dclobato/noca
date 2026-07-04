#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for trusted client-IP extraction."""

from __future__ import annotations

from types import SimpleNamespace

from shared.services.network_utils.validation import get_ip_from_request


def _request(client_host: str | None, *, xff: str | None = None) -> SimpleNamespace:
    """Build a minimal request-like object."""
    client = SimpleNamespace(host=client_host) if client_host is not None else None
    headers = {"X-Forwarded-For": xff} if xff is not None else {}
    return SimpleNamespace(client=client, headers=headers)


def test_returns_validated_client_host() -> None:
    assert get_ip_from_request(_request("203.0.113.9")) == "203.0.113.9"


def test_ignores_spoofed_x_forwarded_for() -> None:
    request = _request("203.0.113.9", xff="198.51.100.1")

    assert get_ip_from_request(request) == "203.0.113.9"


def test_ignores_x_forwarded_for_when_client_missing() -> None:
    request = _request(None, xff="198.51.100.1")

    assert get_ip_from_request(request) is None


def test_invalid_client_host_returns_none() -> None:
    assert get_ip_from_request(_request("not-an-ip")) is None


def test_overlong_client_host_returns_none() -> None:
    assert get_ip_from_request(_request("1" * 46)) is None
