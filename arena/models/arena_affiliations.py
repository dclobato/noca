#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for externally managed Arena affiliations."""

from __future__ import annotations

from base64 import b64decode
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from arena.models.mixins import LocationMixin
from shared.db_schema.arena import arena_affiliations as arena_affiliations_table
from shared.db_schema.arena import arena_users as arena_users_table

if TYPE_CHECKING:
    from arena.models.arena_users import ArenaUser


class ArenaAffiliation(LocationMixin, ArenaBase):
    """ORM model for externally managed Arena user affiliations.

    Attributes:
        id: UUID string primary key.
        name: Affiliation display name.
        country_code: Optional ISO 3166-1 alpha-2 country code.
        subdivision_code: Optional ISO 3166-2 subdivision code.
        url: Optional public URL.
        logo_base64: Optional logo image as base64 string.
        logo_mime: Optional logo MIME type.
        logo_thumbnail_base64: Optional logo thumbnail (64x64) as base64 string.
        created_at: Record creation timestamp.
        updated_at: Record update timestamp.
        users: Arena users currently linked to this affiliation.
    """

    __table__ = arena_affiliations_table

    id: Mapped[str]
    name: Mapped[str]
    country_code: Mapped[str | None]
    subdivision_code: Mapped[str | None]
    url: Mapped[str | None]
    logo_base64: Mapped[str | None]
    logo_mime: Mapped[str | None]
    logo_thumbnail_base64: Mapped[str | None]
    rating: Mapped[int | None]
    dta_rating_update: Mapped[datetime | None]
    exclude_from_ranking: Mapped[bool]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    users: Mapped[list[ArenaUser]] = relationship(
        "ArenaUser",
        back_populates="affiliation",
        foreign_keys=[arena_users_table.c.affiliation_id],
    )

    def apply_logo(
        self,
        *,
        logo_base64: str,
        logo_mime: str,
        logo_thumbnail_base64: str | None = None,
    ) -> None:
        """Store pre-processed logo data directly.

        Args:
            logo_base64: Base64-encoded logo image.
            logo_mime: MIME type of the logo image.
            logo_thumbnail_base64: Optional base64-encoded logo thumbnail (64x64).

        Raises:
            ValueError: If logo_base64 or logo_mime are empty or None.
        """
        if not logo_base64 or not logo_mime:
            raise ValueError("logo_base64 and logo_mime are both required")
        self.logo_base64 = logo_base64
        self.logo_mime = logo_mime
        self.logo_thumbnail_base64 = logo_thumbnail_base64

    def clear_logo(self) -> None:
        """Clear all logo-related fields without committing."""
        self.logo_base64 = None
        self.logo_mime = None
        self.logo_thumbnail_base64 = None

    @property
    def logo_thumbnail(self) -> tuple[bytes, str] | None:
        """Return thumbnail bytes and MIME type, or None if unavailable."""
        if self.logo_thumbnail_base64 and self.logo_mime:
            return b64decode(str(self.logo_thumbnail_base64)), str(self.logo_mime)
        return None
