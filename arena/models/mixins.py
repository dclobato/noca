#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reusable mixins for Arena ORM models."""

from sqlalchemy.orm import Mapped


class LocationMixin:
    """Provide display helpers for models with ISO location codes."""

    country_code: Mapped[str | None]
    subdivision_code: Mapped[str | None]

    @property
    def country_name(self) -> str | None:
        """Return the display name for the stored country code."""
        from arena.services.profile_location_service import country_name

        return country_name(self.country_code)

    @property
    def subdivision_name(self) -> str | None:
        """Return the display name for the stored subdivision code."""
        from arena.services.profile_location_service import subdivision_name

        return subdivision_name(self.subdivision_code)
