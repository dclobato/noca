#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Database side of the Google identity linked to an Arena account.

Every lookup keys on ``google_sub``, never on the email: a Google account's
address can change while its subject identifier cannot, so matching on email
would let a re-used or re-assigned address land on the wrong Arena account.

The caller owns the transaction, per the Arena service convention. These
functions flush where a later step in the same request needs the row to exist,
but they never commit.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from arena.services.google_avatar_service import GoogleAvatarService
from arena.services.google_oauth_service import GoogleIdentityClaims

logger = logging.getLogger(__name__)
_MAX_PICTURE_URL_LENGTH = 2048


def _normalized_picture_url(picture_url: str | None) -> str | None:
    """Return a picture claim that fits persistent storage, or no claim."""
    if picture_url and len(picture_url) <= _MAX_PICTURE_URL_LENGTH:
        return picture_url
    return None


async def get_identity_by_sub(session: AsyncSession, google_sub: str) -> ArenaUserGoogleIdentity | None:
    """Load the Google identity for a subject claim, if one is linked.

    Args:
        session: Active async database session.
        google_sub: Google's stable subject identifier.

    Returns:
        ArenaUserGoogleIdentity | None: The linked identity, or None when this
            Google account is unknown to Arena.
    """
    result = await session.execute(
        select(ArenaUserGoogleIdentity).where(ArenaUserGoogleIdentity.google_sub == google_sub)
    )
    return result.scalar_one_or_none()


async def get_identity_for_user(session: AsyncSession, user_id: str) -> ArenaUserGoogleIdentity | None:
    """Load the Google identity linked to an Arena account, if any.

    Args:
        session: Active async database session.
        user_id: Arena user id.

    Returns:
        ArenaUserGoogleIdentity | None: The linked identity, or None.
    """
    result = await session.execute(select(ArenaUserGoogleIdentity).where(ArenaUserGoogleIdentity.user_id == user_id))
    return result.scalar_one_or_none()


async def link_identity(
    session: AsyncSession,
    *,
    user_id: str,
    claims: GoogleIdentityClaims,
) -> ArenaUserGoogleIdentity:
    """Attach a Google identity to an Arena account.

    Does not pre-check availability. Uniqueness is guaranteed by the two UNIQUE
    constraints on the table, never by a lookup-then-insert, which is a TOCTOU:
    two simultaneous link attempts would both see the row as free. The caller
    handles the resulting IntegrityError as a stated conflict.

    Args:
        session: Active async database session.
        user_id: Arena account to attach the identity to.
        claims: Verified Google claims.

    Returns:
        ArenaUserGoogleIdentity: The newly created, flushed identity row.
    """
    identity = ArenaUserGoogleIdentity(
        user_id=user_id,
        google_sub=claims.sub,
        google_email=claims.email,
        google_email_verified=claims.email_verified,
        google_picture_url=_normalized_picture_url(claims.picture),
        linked_at=datetime.now(UTC),
    )
    session.add(identity)
    await session.flush()
    logger.info("Linked a Google identity to Arena user %s", user_id)
    return identity


async def unlink_identity(session: AsyncSession, identity: ArenaUserGoogleIdentity) -> None:
    """Detach a Google identity from its Arena account.

    Args:
        session: Active async database session.
        identity: The identity row to remove.
    """
    user_id = identity.user_id
    await session.delete(identity)
    await session.flush()
    logger.info("Unlinked the Google identity of Arena user %s", user_id)


async def touch_last_login(session: AsyncSession, identity: ArenaUserGoogleIdentity) -> None:
    """Record that this Google identity was just used to log in.

    Args:
        session: Active async database session.
        identity: The identity that authenticated.
    """
    identity.last_login_at = datetime.now(UTC)
    await session.flush()


async def update_picture_claim(
    session: AsyncSession,
    identity: ArenaUserGoogleIdentity,
    picture_url: str | None,
) -> None:
    """Persist the latest optional picture claim from a verified Google login.

    Args:
        session: Active async database session.
        identity: Linked Google identity being refreshed.
        picture_url: Optional OpenID Connect picture URL.
    """
    identity.google_picture_url = _normalized_picture_url(picture_url)
    await session.flush()


async def refresh_google_avatar(
    session: AsyncSession,
    *,
    identity: ArenaUserGoogleIdentity,
    user: ArenaUser,
    avatar_service: GoogleAvatarService,
) -> bool:
    """Download the current picture claim and replace its validated local cache.

    The effective avatar revision changes only when Google is the selected
    source and the newly generated bytes differ. Callers deliberately handle
    download failures so authentication can continue with the last valid cache.

    Args:
        session: Active async database session.
        identity: Linked Google identity carrying the current picture URL.
        user: Owning Arena user whose visible avatar may change.
        avatar_service: Bounded Google picture downloader and processor.

    Returns:
        bool: True when the cached avatar bytes or MIME type changed.

    Raises:
        GoogleAvatarError: If the picture cannot be downloaded or processed.
        ValueError: If the identity has no current picture URL.
    """
    if not identity.google_picture_url:
        raise ValueError("Google did not provide a profile picture.")

    processed = await avatar_service.download(identity.google_picture_url)
    changed = (
        identity.google_avatar_base64 != processed.avatar_base64 or identity.google_avatar_mime != processed.mime_type
    )
    identity.google_avatar_base64 = processed.avatar_base64
    identity.google_avatar_mime = processed.mime_type
    identity.google_avatar_refreshed_at = datetime.now(UTC)
    if changed and identity.use_google_avatar:
        user.bump_avatar_revision()
    await session.flush()
    return changed


async def set_google_avatar_selected(
    session: AsyncSession,
    *,
    identity: ArenaUserGoogleIdentity,
    user: ArenaUser,
    selected: bool,
) -> bool:
    """Select Google or Arena as the canonical avatar source.

    Args:
        session: Active async database session.
        identity: User's linked Google identity.
        user: Owning Arena user.
        selected: True for Google, false for the Arena upload or fallback.

    Returns:
        bool: True when the visible source changed.

    Raises:
        ValueError: If Google is selected before a valid cache exists.
    """
    if selected and identity.cached_avatar is None:
        raise ValueError("Google profile picture has not been downloaded.")
    if identity.use_google_avatar == selected:
        return False
    identity.use_google_avatar = selected
    user.bump_avatar_revision()
    await session.flush()
    return True
