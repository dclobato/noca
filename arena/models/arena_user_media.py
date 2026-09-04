#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Profile photo and avatar accessors for the Arena user model.

Owns the stored photo, its resized avatar, and the deterministic SVG fallback
served when no photo was uploaded.

The fallback is seeded on ``username`` rather than on the email address. An
email seed is a confirmation oracle: because the avatar is served publicly and
the generator is deterministic, anyone could render the avatar for a guessed
address and compare it against a user's, turning a public image into a test for
"does this person hold this mailbox". Web has always seeded on its username
(``web/models/users.py``); this matches it.
"""

from __future__ import annotations

from base64 import b64decode
from datetime import UTC, datetime
from functools import lru_cache

from deterministic_avatar import DeterministicAvatar
from sqlalchemy.orm import Mapped

_avatar_generator = DeterministicAvatar()


@lru_cache(maxsize=256)
def _generated_avatar(username: str) -> bytes:
    """Render (and memoize) the deterministic SVG avatar for a handle.

    The result depends only on the seed, so it is safe to cache. The anonymous
    dashboard renders a row of avatars per request, and regenerating the SVG
    each time showed up there; ``web/models/users.py`` caches for the same
    reason and with the same bound.

    Args:
        username: The handle to seed the generator with.

    Returns:
        bytes: The UTF-8 encoded SVG document.
    """
    return _avatar_generator.generate_avatar(seed=username, formal=True).encode("utf-8")


def _utcnow() -> datetime:
    """Return current UTC datetime.

    Returns:
        datetime: Current UTC time as timezone-aware datetime.
    """
    return datetime.now(UTC)


class ArenaUserMediaMixin:
    """Provide profile photo and avatar accessors for the Arena user model."""

    username: Mapped[str]
    com_foto: Mapped[bool]
    foto_base64: Mapped[str | None]
    avatar_base64: Mapped[str | None]
    foto_mime: Mapped[str | None]
    dta_foto: Mapped[datetime | None]
    avatar_revision: Mapped[int]

    def bump_avatar_revision(self) -> None:
        """Invalidate URLs that cache the user's effective avatar."""
        self.avatar_revision = (self.avatar_revision or 0) + 1

    @property
    def foto(self) -> tuple[bytes, str]:
        """Return the profile photo as raw bytes and its MIME type.

        Falls back to a deterministic SVG avatar when no photo is stored.

        Returns:
            tuple[bytes, str]: Photo bytes and MIME type.
        """
        if self.com_foto:
            return b64decode(str(self.foto_base64)), self.foto_mime or "application/octet-stream"
        return _generated_avatar(self.username), "image/svg+xml"

    @property
    def avatar(self) -> tuple[bytes, str]:
        """Return the avatar as raw bytes and its MIME type.

        Falls back to the same deterministic SVG avatar as foto when no photo
        is stored.

        Returns:
            tuple[bytes, str]: Avatar bytes and MIME type.
        """
        if self.com_foto:
            return b64decode(str(self.avatar_base64)), self.foto_mime or "application/octet-stream"
        return _generated_avatar(self.username), "image/svg+xml"

    def apply_processed_photo(
        self,
        *,
        foto_base64: str,
        avatar_base64: str,
        mime_type: str,
    ) -> None:
        """Store pre-processed photo data directly.

        Use this when the photo has already been processed by an external
        image-processing service.

        Args:
            foto_base64: Base64-encoded full-size photo.
            avatar_base64: Base64-encoded resized avatar.
            mime_type: MIME type of the photo.

        Raises:
            ValueError: If any argument is empty or None.
        """
        if not foto_base64 or not avatar_base64 or not mime_type:
            raise ValueError("foto_base64, avatar_base64, and mime_type are all required")
        self.foto_base64 = foto_base64
        self.avatar_base64 = avatar_base64
        self.foto_mime = mime_type
        self.dta_foto = _utcnow()
        self.com_foto = True
        self.bump_avatar_revision()

    def clear_foto_fields(self) -> None:
        """Clear all photo-related fields without committing.

        Sets com_foto=False and nulls foto_base64, avatar_base64, foto_mime,
        and dta_foto.
        """
        self.com_foto = False
        self.foto_base64 = None
        self.avatar_base64 = None
        self.foto_mime = None
        self.dta_foto = None
        self.bump_avatar_revision()
