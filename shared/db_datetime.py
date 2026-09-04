#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Normalization for timestamps read back from the database.

PostgreSQL returns aware values for ``DateTime(timezone=True)`` columns while
SQLite returns naive ones. NOCA persists every such column in UTC, so a naive
value read back is UTC that lost its label and is safe to relabel.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

__all__ = ["as_utc", "utc_day"]


def as_utc(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC timestamp.

    Args:
        value: Timestamp read from a ``DateTime(timezone=True)`` column.

    Returns:
        datetime: Timezone-aware UTC timestamp.
    """
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC)


def utc_day(value: datetime) -> date:
    """Return the UTC calendar day of ``value``.

    Args:
        value: Timestamp read from a ``DateTime(timezone=True)`` column.

    Returns:
        date: Calendar date after normalization to UTC.
    """
    return as_utc(value).date()
