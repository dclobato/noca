#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add detailed geolocation columns to login history and drop free-text location.

Revision ID: 202607100001
Revises: 202607090001
Create Date: 2026-07-10

Adds structured per-IP geolocation columns (country_code, subdivision_code,
district, city, is_eu, as_number) to both ``arena_login_history`` and
``login_history`` and drops the legacy free-text ``location`` column. Historical
``location`` strings are discarded; existing rows are re-derived from their still
present ``ip_address`` by the standalone, re-runnable backfill script
``scripts/backfill_login_geolocation.py`` (which calls the ipgeolocation.io API).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607100001"
down_revision: str | Sequence[str] | None = "202607090001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("arena_login_history", "login_history")

_COLUMNS = (
    ("country_code", sa.String(2), "ISO 3166-1 alpha-2 country code from IP"),
    ("subdivision_code", sa.String(16), "ISO 3166-2 subdivision code from IP"),
    ("district", sa.String(128), "District/county name from IP"),
    ("city", sa.String(128), "City name from IP"),
    ("is_eu", sa.Boolean(), "Whether the IP country is in the EU"),
    ("as_number", sa.String(16), "Autonomous System number from IP"),
)


def upgrade() -> None:
    """Add the structured geolocation columns and drop the legacy location column."""
    for table in _TABLES:
        for name, column_type, comment in _COLUMNS:
            op.add_column(table, sa.Column(name, column_type, nullable=True, comment=comment))
        op.drop_column(table, "location")


def downgrade() -> None:
    """Re-add the legacy location column (data not restored) and drop the new columns."""
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("location", sa.String(128), nullable=True, comment="Geographic location derived from IP"),
        )
        for name, _column_type, _comment in reversed(_COLUMNS):
            op.drop_column(table, name)
