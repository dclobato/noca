#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared email-reputation service backed by the IPQualityScore API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from shared.services.network_utils import NetworkService, NetworkServiceError


@dataclass(frozen=True)
class EmailReputation:
    """Reputation signals for a single email address from IPQualityScore.

    Attributes:
        valid: Whether the mailbox is inferred to exist and accept mail.
        disposable: Whether the address belongs to a disposable/temporary provider.
        suspect: Whether the address shows suspicious characteristics.
        overall_score: Overall confidence score (0-4) that the address is valid.
        common: Whether the address matches common/spam-trap-like patterns.
        fraud_score: Fraud/abuse likelihood (0-100); higher is riskier.
        sanitized_email: The corrected/sanitized form of the submitted address.
    """

    valid: bool
    disposable: bool
    suspect: bool
    overall_score: int
    common: bool
    fraud_score: int
    sanitized_email: str


def _as_bool(value: Any) -> bool:
    """Return a genuine boolean, defaulting to ``False`` for any other type."""
    return value if isinstance(value, bool) else False


def _as_int(value: Any) -> int:
    """Return a genuine integer, defaulting to ``0`` (rejecting ``bool``)."""
    if isinstance(value, bool):
        return 0
    return value if isinstance(value, int) else 0


def _as_str(value: Any, fallback: str) -> str:
    """Return a non-empty stripped string, otherwise ``fallback``."""
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed:
            return trimmed
    return fallback


class EmailReputationService:
    """Service to assess email reputation using the IPQualityScore API."""

    def __init__(
        self,
        api_key: str | None,
        network_service: NetworkService,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the EmailReputationService.

        Args:
            api_key: IPQualityScore API key. If None, reputation lookups are disabled.
            network_service: A NetworkService instance used to make HTTP requests.
            logger: Optional logger; a module logger is used when omitted.
        """
        self.api_key = api_key
        self._base_url = "https://www.ipqualityscore.com/api/json/email"
        self._network = network_service
        self._logger = logger or logging.getLogger(__name__)

    def check(self, email: str) -> EmailReputation | None:
        """Look up reputation signals for an email address.

        Args:
            email: The email address to evaluate.

        Returns:
            An ``EmailReputation`` with the extracted signals, or None if the
            service is disabled (no API key) or the lookup fails.
        """
        if self.api_key is None:
            return None

        url = f"{self._base_url}/{quote(self.api_key, safe='')}/{quote(email, safe='')}"

        try:
            response = self._network.make_json_request(url=url, params={"timeout": 7})
        except (NetworkServiceError, ValueError) as e:
            self._logger.error("Failed to get email reputation for %s: %s", email, e)
            return None

        if not _as_bool(response.get("success")):
            self._logger.error(
                "Email reputation lookup for %s was unsuccessful: %s",
                email,
                response.get("message", "no message"),
            )
            return None

        return EmailReputation(
            valid=_as_bool(response.get("valid")),
            disposable=_as_bool(response.get("disposable")),
            suspect=_as_bool(response.get("suspect")),
            overall_score=_as_int(response.get("overall_score")),
            common=_as_bool(response.get("common")),
            fraud_score=_as_int(response.get("fraud_score")),
            sanitized_email=_as_str(response.get("sanitized_email"), email),
        )
