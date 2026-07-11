#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import logging
import time
from enum import StrEnum
from typing import Any, cast

from jwtservice import JWTService
from jwtservice.core import TokenVerificationResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from werkzeug.security import check_password_hash

from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationIP
from web.config import settings
from web.models.users import Login_History, UberAdmin, User
from web.services.contest_user_service import normalize_username


class AuthAction(StrEnum):
    WEB_ACCESS = "web_access"
    API_ACCESS = "api_access"


_SESSION_STARTED_AT_CLAIM = "session_started_at"


class AuthenticationService:
    """Authenticate UberAdmins and contest users and issue JWT access tokens.

    This service validates submitted credentials against persisted users, creates
    web access JWTs for successful logins, and records login history entries with
    optional IP, user-agent, and geolocation metadata.

    Notes:
        Logout token revocation is backed by a ``ValkeyRevocationStore`` when one is
        configured in the application lifespan. Revoked JTIs are stored with a TTL
        matching the token's remaining lifetime so entries expire automatically.
    """

    def __init__(
        self, jwt_service: JWTService, geolocation_service: GeolocationIP, logger: logging.Logger | None = None
    ) -> None:
        """Initialize the authentication service dependencies.

        Args:
            jwt_service: JWT service used to create and validate access tokens.
            geolocation_service: Geolocation provider used to resolve login IP
                addresses into approximate locations for audit history.
            logger: Optional logger instance. When omitted, a module logger is used.

        Returns:
            None.
        """
        self._jwt = jwt_service
        self._geo = geolocation_service
        self._logger = logger or logging.getLogger(__name__)

    @property
    def jwt_service(self) -> JWTService:
        """Expose the configured JWT service used by this authentication service.

        Returns:
            The configured `JWTService` instance.
        """
        return self._jwt

    @staticmethod
    def _now_epoch_seconds() -> int:
        """Return the current Unix timestamp in whole seconds."""
        return int(time.time())

    def _build_extra_data(
        self,
        extra_data: dict[str, object] | None,
        *,
        session_started_at: int | None,
    ) -> dict[str, object] | None:
        """Return token extra data with an optional original session start marker."""
        data = dict(extra_data or {})
        if session_started_at is not None:
            data[_SESSION_STARTED_AT_CLAIM] = session_started_at
        return data or None

    def create_access_token(
        self,
        *,
        sub: str,
        audience: str,
        extra_data: dict[str, object] | None = None,
        session_started_at: int | None = None,
    ) -> str:
        """Create a signed web access token with the current configured lifetime."""
        return cast(
            str,
            self._jwt.create(
                action=AuthAction.WEB_ACCESS,
                sub=sub,
                audience=audience,
                extra_data=self._build_extra_data(extra_data, session_started_at=session_started_at),
                expires_in=settings.JWT_EXPIRE_SECONDS,
            ),
        )

    def get_session_started_at(self, result: TokenVerificationResult) -> int | None:
        """Return the original session start timestamp when present and valid."""
        started_at = (result.extra_data or {}).get(_SESSION_STARTED_AT_CLAIM)
        return started_at if isinstance(started_at, int) else None

    def should_refresh_token(self, result: TokenVerificationResult) -> bool:
        """Return whether a valid web token is inside the half-life refresh window."""
        if not result.valid or result.action != AuthAction.WEB_ACCESS:
            return False
        expires_in = result.expires_in
        if expires_in is None:
            return False
        refresh_threshold = max(1, settings.JWT_EXPIRE_SECONDS // 2)
        return cast(bool, expires_in <= refresh_threshold)

    def is_absolute_session_cap_exceeded(self, result: TokenVerificationResult) -> bool:
        """Return whether the optional sliding-session cap has been exceeded."""
        max_session_seconds = settings.JWT_REFRESH_MAX_SESSION_SECONDS
        if max_session_seconds <= 0 or not result.valid:
            return False
        session_started_at = self.get_session_started_at(result)
        if session_started_at is None:
            return False
        return self._now_epoch_seconds() - session_started_at >= max_session_seconds

    def build_refreshed_access_token(self, result: TokenVerificationResult) -> str | None:
        """Return a fresh token for an authenticated web session when eligible."""
        if not self.should_refresh_token(result):
            return None
        if self.is_absolute_session_cap_exceeded(result):
            return None

        session_started_at = self.get_session_started_at(result)
        if settings.JWT_REFRESH_MAX_SESSION_SECONDS > 0 and session_started_at is None:
            return None

        extra_data = dict(result.extra_data or {})
        return self.create_access_token(
            sub=result.sub,
            audience=result.aud,
            extra_data=extra_data,
            session_started_at=session_started_at,
        )

    def _record_login_history(
        self,
        session: AsyncSession,
        *,
        user_id: str | None = None,
        uberadmin_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Add a login history entry for a successful authentication event.

        Args:
            session: Database session that will receive the new `Login_History` row.
            user_id: Contest user identifier for user logins.
            uberadmin_id: UberAdmin identifier for UberAdmin logins.
            ip_address: Optional client IP address to persist and geolocate.
            user_agent: Optional client user-agent string to persist.

        Returns:
            None.

        Raises:
            ValueError: If neither actor ID is provided or if both actor IDs are provided.

        Notes:
            This helper only adds the `Login_History` row to the session. The caller
            remains responsible for committing the transaction.
        """
        if (user_id is None) == (uberadmin_id is None):
            raise ValueError("Exactly one of user_id or uberadmin_id must be provided.")

        args: dict[str, Any] = {
            "user_id": user_id,
            "uberadmin_id": uberadmin_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
        }
        details = self._geo.get_details_by_ip(ip_address) if ip_address else None
        if details is not None:
            args["country_code"] = details.country_code
            args["subdivision_code"] = details.subdivision_code
            args["district"] = details.district
            args["city"] = details.city
            args["is_eu"] = details.is_eu
            args["as_number"] = details.as_number

        session.add(Login_History(**args))

    async def uberadmin_login(
        self,
        username: str,
        password: str,
        session: AsyncSession,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """Authenticate an UberAdmin and return a signed web access token.

        Args:
            username: Submitted UberAdmin username or identifier. It is normalized
                before database lookup.
            password: Plaintext password submitted by the user.
            session: Database session used to load the UberAdmin and persist login history.
            ip_address: Optional client IP address stored in login history and used
                for geolocation lookup.
            user_agent: Optional client user-agent string stored in login history.

        Returns:
            A signed JWT string for the authenticated UberAdmin.

        Raises:
            ValueError: If the username does not exist or the password is invalid.

        Notes:
            On success, this method writes a `Login_History` row and commits the
            session before returning the token.
        """
        username = normalize_username(username)
        result = await session.execute(select(UberAdmin).where(UberAdmin.username == username))
        user = result.scalar_one_or_none()

        if user is None or not check_password_hash(user.password_hash, password):
            raise ValueError("invalid_credentials")
        if not user.is_enabled:
            raise ValueError("invalid_credentials")

        token = self.create_access_token(
            sub=user.username,
            audience=RoleEnum.UBERADMIN.value,
            session_started_at=self._now_epoch_seconds(),
        )

        self._record_login_history(
            session,
            uberadmin_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await session.commit()
        return token

    async def user_login(
        self,
        username: str,
        password: str,
        contest_id: str,
        session: AsyncSession,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """Authenticate a contest user and return a signed contest-scoped access token.

        Args:
            username: Submitted contest username or identifier. It is normalized
                before database lookup.
            password: Plaintext password submitted by the user.
            contest_id: Contest identifier the user must belong to.
            session: Database session used to load the user and persist login history.
            ip_address: Optional client IP address stored in login history and used
                for geolocation lookup.
            user_agent: Optional client user-agent string stored in login history.

        Returns:
            A signed JWT string for the authenticated contest user. The token
            includes the user's role as audience and the contest ID in `extra_data`.

        Raises:
            ValueError: If the user does not exist in the given contest or the
                password is invalid.

        Notes:
            On success, this method writes a `Login_History` row and commits the
            session before returning the token.
        """
        username = normalize_username(username)
        result = await session.execute(select(User).where(User.username == username, User.contest_id == contest_id))
        user = result.scalar_one_or_none()

        if user is None or not check_password_hash(user.password_hash, password):
            raise ValueError("invalid_credentials")

        token = self.create_access_token(
            sub=user.username,
            audience=user.role.value,
            extra_data={"contest_id": contest_id},
            session_started_at=self._now_epoch_seconds(),
        )

        self._record_login_history(
            session,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await session.commit()
        return token

    def logout(self, token: str) -> None:
        """Revoke the given access token so it cannot be replayed after logout.

        Args:
            token: The raw JWT string to revoke. The JTI and remaining TTL are
                extracted internally by ``jwtservice`` and written to the configured
                revocation store.

        Returns:
            None.

        Notes:
            Revocation is best-effort: if no revocation store is configured, or if
            the store is temporarily unavailable, this call is a no-op and the token
            remains valid until its natural expiry.
        """
        self._jwt.revoke(token, reason="logout")
