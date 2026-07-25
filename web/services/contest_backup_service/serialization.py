#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Lossless, column-driven serialization between DB rows and JSON dicts.

The backup format stores each row as a plain JSON object keyed by column name.
Only two column kinds need special handling round-tripping through JSON:

* ``DateTime`` columns are emitted as ISO-8601 strings and parsed back with
  :func:`datetime.fromisoformat`.
* ``Enum`` values are emitted as their string ``.value`` and accepted verbatim
  on insert (SQLAlchemy's ``Enum`` bind processor maps the raw value back).

Everything else (``str``, ``int``, ``bool``, ``None``, JSON columns) passes
through untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import DateTime, Table
from sqlalchemy.engine import Row


def row_to_dict(row: Row[Any] | Mapping[str, Any]) -> dict[str, Any]:
    """Serialize one Core result row to a JSON-safe dict keyed by column name."""
    mapping = row._mapping if isinstance(row, Row) else row
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, Enum):
            out[key] = value.value
        else:
            out[key] = value
    return out


def rows_to_dicts(rows: list[Row[Any]]) -> list[dict[str, Any]]:
    """Serialize a list of Core result rows."""
    return [row_to_dict(row) for row in rows]


def build_insert_values(
    table: Table,
    data: Mapping[str, Any],
    *,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Core ``insert()`` values dict from an exported row dict.

    ``DateTime`` columns are parsed from their ISO-8601 string form. Values named
    in ``overrides`` win over the exported data (used to remap foreign keys and
    swap in freshly generated identifiers). Columns absent from both ``data`` and
    ``overrides`` are omitted so their table default applies.

    Args:
        table: Target Core table.
        data: Exported row dict for that table.
        overrides: Column values that replace the exported ones verbatim.

    Returns:
        A values dict suitable for ``session.execute(insert(table), [values])``.
    """
    overrides = overrides or {}
    values: dict[str, Any] = {}
    for column in table.columns:
        name = column.name
        if name in overrides:
            values[name] = overrides[name]
            continue
        if name not in data:
            continue
        raw = data[name]
        if raw is not None and isinstance(column.type, DateTime):
            values[name] = datetime.fromisoformat(raw)
        else:
            values[name] = raw
    return values
