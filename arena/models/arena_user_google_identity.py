#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for the Google (OpenID Connect) identity linked to an Arena account."""

from __future__ import annotations

from base64 import b64decode
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena import (
    arena_user_google_identities as arena_user_google_identities_table,
)

if TYPE_CHECKING:
    from arena.models.arena_users import ArenaUser


class ArenaUserGoogleIdentity(ArenaBase):
    """A Google account linked to an Arena account as an alternative login door.

    The pair of UNIQUE constraints on ``user_id`` and ``google_sub`` states the whole
    invariant: an Arena account has at most one Google identity, and a Google identity
    belongs to at most one Arena account.

    Attributes:
        id: UUID string primary key.
        user_id: FK to arena_users (unique; one Google identity per account).
        google_sub: Google's stable subject claim; the join key, never the email.
        google_email: Google account email at link time, informational only.
        google_email_verified: Whether Google asserted email_verified at link time.
        google_picture_url: Latest optional OpenID Connect picture claim.
        google_avatar_base64: Validated local cache of Google's resized picture.
        google_avatar_mime: Detected MIME type of the cached Google avatar.
        google_avatar_refreshed_at: Last successful picture refresh.
        use_google_avatar: Whether Arena currently serves the Google cache.
        linked_at: When this identity was linked to the Arena account.
        last_login_at: Most recent Google login, or None until the identity is used.
        created_at: Record creation timestamp.
        updated_at: Record last-update timestamp.
        user: Back-reference to the owning ArenaUser.
    """

    __table__ = arena_user_google_identities_table

    id: Mapped[str]
    user_id: Mapped[str]
    google_sub: Mapped[str]
    google_email: Mapped[str | None]
    google_email_verified: Mapped[bool]
    google_picture_url: Mapped[str | None]
    google_avatar_base64: Mapped[str | None]
    google_avatar_mime: Mapped[str | None]
    google_avatar_refreshed_at: Mapped[datetime | None]
    use_google_avatar: Mapped[bool]
    linked_at: Mapped[datetime]
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="google_identity",
        foreign_keys=[arena_user_google_identities_table.c.user_id],
    )

    @property
    def cached_avatar(self) -> tuple[bytes, str] | None:
        """Return the cached Google avatar when its stored fields are complete.

        Returns:
            tuple[bytes, str] | None: Decoded avatar bytes and MIME type, or
                ``None`` when no valid cache has been persisted.
        """
        if not self.google_avatar_base64 or not self.google_avatar_mime:
            return None
        return b64decode(self.google_avatar_base64), self.google_avatar_mime
