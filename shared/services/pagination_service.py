#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared pagination helpers for server-rendered pages."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Pagination[T]:
    """Pagination result compatible with Jinja templates.

    Attributes:
        items: Records on the current page.
        page: Current one-based page number.
        per_page: Number of records per page.
        total: Total number of matching records.
    """

    items: Sequence[T]
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        """Return the total number of pages."""
        if self.total <= 0:
            return 0
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_prev(self) -> bool:
        """Return whether a previous page exists."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """Return whether a next page exists."""
        return self.page < self.pages

    @property
    def prev_num(self) -> int | None:
        """Return the previous page number, if any."""
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self) -> int | None:
        """Return the next page number, if any."""
        return self.page + 1 if self.has_next else None

    @property
    def first(self) -> int:
        """Return the one-based index of the first item on the current page."""
        if self.total == 0:
            return 0
        return (self.page - 1) * self.per_page + 1

    @property
    def last(self) -> int:
        """Return the one-based index of the last item on the current page."""
        if self.total == 0:
            return 0
        return min(self.page * self.per_page, self.total)

    def iter_pages(
        self,
        *,
        left_edge: int = 2,
        left_current: int = 2,
        right_current: int = 3,
        right_edge: int = 2,
    ) -> Iterator[int | None]:
        """Yield visible page numbers, using ``None`` for omitted ranges.

        Args:
            left_edge: Number of pages to show at the start.
            left_current: Number of pages to show before the current page.
            right_current: Number of pages to show after the current page.
            right_edge: Number of pages to show at the end.

        Yields:
            int | None: A page number or ``None`` for an omitted range.
        """
        last = 0
        for number in range(1, self.pages + 1):
            is_left_edge = number <= left_edge
            is_near_current = self.page - left_current - 1 < number < self.page + right_current
            is_right_edge = number > self.pages - right_edge
            if is_left_edge or is_near_current or is_right_edge:
                if last + 1 != number:
                    yield None
                yield number
                last = number


def clamp_page(page: int, *, total: int, per_page: int) -> int:
    """Clamp a page to the available range for a known total.

    Args:
        page: Requested page number.
        total: Total row count.
        per_page: Page size.

    Returns:
        int: Page number between one and the last page.
    """
    if total <= 0:
        return 1
    pages = (total + per_page - 1) // per_page
    return min(max(1, page), pages)


def parse_page(value: str | int | None, *, default: int = 1) -> int:
    """Parse a page value and clamp it to one or greater.

    Args:
        value: Raw page value from a request.
        default: Fallback page when the value is missing or invalid.

    Returns:
        int: Valid one-based page number.
    """
    try:
        page = int(value if value is not None else default)
    except TypeError, ValueError:
        page = default
    return max(1, page)


def effective_per_page(value: str | int | None, *, allowed: set[int], default: int) -> int:
    """Return a validated page size from a request value.

    Args:
        value: Raw page-size value from a request.
        allowed: Set of accepted page sizes.
        default: Fallback when the value is absent, invalid, or disallowed.

    Returns:
        int: A member of ``allowed`` or ``default``.
    """
    try:
        resolved = int(value) if value else default
    except TypeError, ValueError:
        return default
    return resolved if resolved in allowed else default
