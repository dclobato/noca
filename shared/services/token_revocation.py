#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared JWT token revocation store implementations."""

import logging

import valkey as sync_valkey


class ValkeyRevocationStore:
    """Sync RevocationStore protocol implementation backed by Valkey.

    Implements the two-method protocol expected by ``jwtservice.JWTService`` so that
    ``validate()`` automatically rejects revoked tokens and ``revoke()`` persists JTI
    entries with a TTL matching the token's remaining lifetime.

    Both methods fail-open on Valkey errors: ``is_revoked`` returns ``False`` (session
    continues) and ``revoke`` logs a warning but does not raise. This preserves
    availability during transient Valkey outages at the cost of not enforcing revocation
    until connectivity is restored.
    """

    _KEY_PREFIX = "auth:revoked"

    def __init__(self, valkey_url: str, logger: logging.Logger | None = None) -> None:
        """Open a synchronous Valkey connection for revocation checks.

        Args:
            valkey_url: Valkey connection URL (same URL used by the async runtime).
            logger: Optional logger; falls back to the module logger when omitted.
        """
        self._client = sync_valkey.Valkey.from_url(valkey_url, decode_responses=True)
        self._logger = logger or logging.getLogger(__name__)

    def _key(self, jti: str) -> str:
        return f"{self._KEY_PREFIX}:{jti}"

    def is_revoked(self, jti: str) -> bool:
        """Return True if the JTI is present in the revocation store.

        Args:
            jti: The JWT ID claim extracted from the token being validated.

        Returns:
            ``True`` when the token has been explicitly revoked, ``False`` otherwise
            (including when Valkey is unavailable — fail-open).
        """
        try:
            return bool(self._client.exists(self._key(jti)))
        except Exception:
            self._logger.warning("Revocation check failed for jti=%s; failing open", jti, exc_info=True)
            return False

    def revoke(self, jti: str, ttl_seconds: int, metadata: object = None) -> bool:
        """Store a revoked JTI with a TTL so it expires when the token would have.

        Args:
            jti: The JWT ID claim to revoke.
            ttl_seconds: Seconds until the revocation entry should be auto-deleted.
            metadata: Optional metadata passed by jwtservice (not persisted).

        Returns:
            ``True`` if the JTI was newly revoked, ``False`` if already present or if
            Valkey is unavailable.
        """
        try:
            result = self._client.set(self._key(jti), "1", nx=True, ex=max(1, ttl_seconds))
            self._logger.debug("Token %s revoked", jti)
            return bool(result)
        except Exception:
            self._logger.warning("Failed to revoke jti=%s; token may remain replayable", jti, exc_info=True)
            return False

    def close(self) -> None:
        """Close the underlying synchronous Valkey connection."""
        self._client.close()  # type: ignore[no-untyped-call]
