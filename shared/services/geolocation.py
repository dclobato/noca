#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared IP geolocation service."""

import logging
from dataclasses import dataclass
from typing import Any

from shared.services.network_utils import NetworkService, NetworkServiceError


@dataclass(frozen=True)
class GeolocationDetails:
    """Structured geolocation fields resolved for a single IP address.

    Attributes:
        country_code: ISO 3166-1 alpha-2 country code (upper-case) or None.
        subdivision_code: ISO 3166-2 subdivision code (e.g. ``DE-BY``) or None.
        district: Free-text district/county name or None.
        city: Free-text city name or None.
        is_eu: Whether the country is part of the EU, or None if unknown.
        as_number: Autonomous System number as a string (e.g. ``AS24940``) or None.
    """

    country_code: str | None
    subdivision_code: str | None
    district: str | None
    city: str | None
    is_eu: bool | None
    as_number: str | None


# Maximum lengths mirror the bounded columns these values are written into
# (``arena_login_history`` / ``login_history``). Anything longer is treated as a
# malformed provider value and discarded so persistence can never fail on length.
_MAX_SUBDIVISION_LEN = 16
_MAX_DISTRICT_LEN = 128
_MAX_CITY_LEN = 128
_MAX_AS_NUMBER_LEN = 16


def _clean_str(value: Any, *, max_len: int | None = None) -> str | None:
    """Return a stripped string only for genuine, non-empty ``str`` inputs.

    Args:
        value: Arbitrary JSON value.
        max_len: Optional maximum length; values longer than this are discarded
            (returns None) so they never overflow a bounded database column.

    Returns:
        The stripped string, or None when the value is not a non-empty string
        within the length bound.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if max_len is not None and len(trimmed) > max_len:
        return None
    return trimmed


def _clean_country_code(value: Any) -> str | None:
    """Normalize an ISO 3166-1 alpha-2 country code from arbitrary JSON."""
    trimmed = _clean_str(value)
    if trimmed is None:
        return None
    normalized = trimmed.upper()
    if len(normalized) == 2 and normalized.isalpha():
        return normalized
    return None


def _clean_subdivision_code(value: Any, country_code: str | None) -> str | None:
    """Normalize an ISO 3166-2 subdivision code, guarding the country prefix.

    The subdivision code is only accepted when it is prefixed by the resolved
    country code (e.g. ``DE-BY`` for ``DE``) and fits the bounded column; anything
    else is discarded so no malformed provider value flows into the column.

    Args:
        value: Arbitrary JSON value for the subdivision code.
        country_code: The already-normalized country code, or None.

    Returns:
        The normalized subdivision code, or None.
    """
    if country_code is None:
        return None
    trimmed = _clean_str(value, max_len=_MAX_SUBDIVISION_LEN)
    if trimmed is None:
        return None
    normalized = trimmed.upper()
    if normalized.startswith(f"{country_code}-"):
        return normalized
    return None


def _clean_as_number(value: Any) -> str | None:
    """Normalize an AS number to a bounded string, accepting str or int inputs."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = str(value)
        return candidate if len(candidate) <= _MAX_AS_NUMBER_LEN else None
    return _clean_str(value, max_len=_MAX_AS_NUMBER_LEN)


class GeolocationIP:
    """Service to get geolocation information based on IP address using ipgeolocation.io API."""

    def __init__(self, api_key: str | None, network_service: NetworkService, logger: logging.Logger | None = None):
        """Initialize the GeolocationIP service.

        Args:
            api_key: API key for ipgeolocation.io. If None, geolocation will be disabled.
            network_service: An instance of NetworkService to make HTTP requests.
            logger: Optional logger for logging errors. If None, a default logger will be used.
        """
        self.api_key = api_key
        self._base_url = "https://api.ipgeolocation.io/v3/ipgeo"
        self._network = network_service
        self._logger = logger or logging.getLogger(__name__)

    def get_details_by_ip(self, ip_address: str) -> GeolocationDetails | None:
        """Retrieve structured geolocation information for a given IP address.

        Args:
            ip_address: The IP address to look up.

        Returns:
            A ``GeolocationDetails`` with normalized, type-guarded fields, or None
            if geolocation is disabled, the IP is private, or an error occurs.
        """
        if self.api_key is None:
            return None

        if NetworkService.is_private_network(ip_address):
            return None

        try:
            response = self._network.make_json_request(
                url=self._base_url,
                params={"apiKey": self.api_key, "ip": ip_address},
            )
        except (NetworkServiceError, ValueError) as e:
            self._logger.error("Failed to get geolocation for IP %s: %s", ip_address, e)
            return None

        location = response.get("location")
        location = location if isinstance(location, dict) else {}
        asn = response.get("asn")
        asn = asn if isinstance(asn, dict) else {}

        country_code = _clean_country_code(location.get("country_code2"))
        is_eu_raw = location.get("is_eu")
        return GeolocationDetails(
            country_code=country_code,
            subdivision_code=_clean_subdivision_code(location.get("state_code"), country_code),
            district=_clean_str(location.get("district"), max_len=_MAX_DISTRICT_LEN),
            city=_clean_str(location.get("city"), max_len=_MAX_CITY_LEN),
            is_eu=is_eu_raw if isinstance(is_eu_raw, bool) else None,
            as_number=_clean_as_number(asn.get("as_number")),
        )
