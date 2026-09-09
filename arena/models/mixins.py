#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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


class BadgeColorMixin:
    """Provide readable badge text for models storing a hex ``color``.

    Used by categories, the one taxonomy that carries a badge color, so the
    contrast rule lives somewhere a second colored model could reuse rather than
    reimplement.
    """

    color: Mapped[str]

    @property
    def foreground_color(self) -> str:
        """Return black or white, whichever has better WCAG contrast with ``color``.

        Returns:
            str: ``"#000000"`` for black text or ``"#ffffff"`` for white text.
        """
        hex_color = self.color.removeprefix("#")
        red = int(hex_color[0:2], 16) / 255
        green = int(hex_color[2:4], 16) / 255
        blue = int(hex_color[4:6], 16) / 255

        def linear(channel: float) -> float:
            """Convert an sRGB channel to linear light."""
            if channel <= 0.03928:
                return channel / 12.92
            return float(((channel + 0.055) / 1.055) ** 2.4)

        luminance = 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
        contrast_with_black = (luminance + 0.05) / 0.05
        contrast_with_white = 1.05 / (luminance + 0.05)
        return "#000000" if contrast_with_black >= contrast_with_white else "#ffffff"
