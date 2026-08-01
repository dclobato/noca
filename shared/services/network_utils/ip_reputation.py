#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""IPQualityScore IP reputation service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from shared.log_redaction import redact_secrets

from .errors import NetworkServiceError
from .service import NetworkService


@dataclass(frozen=True)
class IPReputation:
    """Reputation signals for a single IP address from IPQualityScore.

    Attributes:
        is_crawler: Whether the IP is associated with a confirmed search crawler.
        mobile: Whether the optional user-agent signal indicated a mobile browser.
        recent_abuse: Whether IPQS has recently verified abuse for the IP.
        fraud_score: Fraud/abuse likelihood from 0 to 100; higher is riskier.
        proxy: Whether the IP is suspected to be a proxy.
        vpn: Whether the IP is suspected to be a VPN.
        tor: Whether the IP is suspected to be a Tor connection.
        active_vpn: Whether the IP is an active VPN connection.
        active_tor: Whether the IP is an active Tor exit.
    """

    is_crawler: bool
    mobile: bool
    recent_abuse: bool
    fraud_score: int
    proxy: bool
    vpn: bool
    tor: bool
    active_vpn: bool
    active_tor: bool


def _as_bool(value: Any) -> bool:
    """Return a genuine boolean, defaulting to ``False`` for any other type."""
    return value if isinstance(value, bool) else False


def _as_int(value: Any) -> int:
    """Return a genuine integer, defaulting to ``0`` and rejecting ``bool``."""
    if isinstance(value, bool):
        return 0
    return value if isinstance(value, int) else 0


class IPQualityScoreIPReputationService:
    """Service to assess IP reputation using the IPQualityScore proxy API."""

    def __init__(
        self,
        api_key: str | None,
        network_service: NetworkService,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the IP reputation service.

        Args:
            api_key: IPQualityScore API key. If None, reputation lookups are disabled.
            network_service: A NetworkService instance used to make HTTP requests.
            logger: Optional logger; a module logger is used when omitted.
        """
        self.api_key = api_key
        self._base_url = "https://www.ipqualityscore.com/api/json/ip"
        self._network = network_service
        self._logger = logger or logging.getLogger(__name__)

    def check(self, ip_address: str) -> IPReputation | None:
        """Look up reputation signals for an IP address.

        Args:
            ip_address: The IP address to evaluate.

        Returns:
            An ``IPReputation`` with extracted signals, or None if the service
            is disabled, the IP is private, or the lookup fails.
        """
        if self.api_key is None:
            return None

        if NetworkService.is_private_network(ip_address):
            return None

        url = f"{self._base_url}/{quote(self.api_key, safe='')}/{quote(ip_address, safe='')}"

        try:
            response = self._network.make_json_request(
                url=url,
                params={"strictness": 1, "allow_public_access_points": "true"},
            )
        except (NetworkServiceError, ValueError) as e:
            # NetworkServiceError messages embed the failed URL, and the API key
            # travels as one of its path segments.
            self._logger.error("Failed to get IP reputation for %s: %s", ip_address, redact_secrets(str(e)))
            return None

        if not _as_bool(response.get("success")):
            self._logger.error(
                "IP reputation lookup for %s was unsuccessful: %s",
                ip_address,
                response.get("message", "no message"),
            )
            return None

        return IPReputation(
            is_crawler=_as_bool(response.get("is_crawler")),
            mobile=_as_bool(response.get("mobile")),
            recent_abuse=_as_bool(response.get("recent_abuse")),
            fraud_score=_as_int(response.get("fraud_score")),
            proxy=_as_bool(response.get("proxy")),
            vpn=_as_bool(response.get("vpn")),
            tor=_as_bool(response.get("tor")),
            active_vpn=_as_bool(response.get("active_vpn")),
            active_tor=_as_bool(response.get("active_tor")),
        )
