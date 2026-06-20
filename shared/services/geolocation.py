#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared IP geolocation service."""

import logging

from shared.services.network_utils import NetworkService, NetworkServiceError


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

    def get_location_by_ip(self, ip_address: str) -> str | None:
        """Retrieve the geolocation information for a given IP address.

        Args:
            ip_address: The IP address to look up.

        Returns:
            A string containing the location information (country, state, district, city) or None
            if geolocation is disabled or if an error occurs.
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

        location = response.get("location") or {}

        country = location.get("country_name", "")
        state = location.get("state_prov", "")
        district = location.get("district", "")
        city = location.get("city", "")
        parts = [country, state, district, city]
        location_str = ", ".join(p for p in parts if p)

        return location_str
