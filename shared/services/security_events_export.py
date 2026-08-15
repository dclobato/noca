#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""CSV export of the shared security-event log.

The export deliberately ignores the viewer's on-screen filters: an operator
downloading the log wants the whole history, not the current page. It does keep
the viewer's *module ownership* scope, which is an authorization boundary rather
than a user filter -- the Web uberadmin viewer owns ``web`` events and the Arena
admin viewer owns ``arena``/``aiassistant`` events.

Rows are streamed in keyset-paginated batches so a large log never has to be
materialized in memory, neither here nor in the response body.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, RowMapping, and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events

# UTF-8 BOM so spreadsheet applications detect the encoding of the download.
_UTF8_BOM = "﻿"
_BATCH_SIZE = 500

CSV_HEADER: tuple[str, ...] = (
    "created_at_utc",
    "module",
    "event_type",
    "severity",
    "actor_user_id",
    "actor_label",
    "identifier_hash",
    "client_ip",
    "source_port",
    "request_id",
    "user_agent",
    "metadata",
)


def csv_filename(prefix: str, *, now: datetime | None = None) -> str:
    """Return a timestamped download filename for a security-event export.

    Args:
        prefix: Short slug identifying the viewer, such as ``web-security-events``.
        now: Timestamp to stamp into the name; defaults to the current UTC time.

    Returns:
        The attachment filename, for example ``web-security-events-20260815-1204.csv``.
    """
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}.csv"


async def stream_security_events_csv(
    session: AsyncSession,
    *,
    module: str | None = None,
    modules: Sequence[str] | None = None,
) -> AsyncIterator[str]:
    """Yield the full security-event log for a module scope as CSV text chunks.

    Args:
        session: Async SQLAlchemy session.
        module: Optional single-module scope, such as ``web``.
        modules: Optional multi-module scope, such as ``["arena", "aiassistant"]``.
            An empty sequence matches nothing.

    Yields:
        CSV text chunks, starting with a UTF-8 BOM and the header row. Rows are
        ordered newest first, matching the on-screen log.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    yield _UTF8_BOM + _drain(buffer)

    conditions: list[ColumnElement[bool]] = []
    if module:
        conditions.append(security_events.c.module == module)
    if modules is not None:
        conditions.append(security_events.c.module.in_(list(modules)))

    cursor: tuple[datetime, str] | None = None
    while True:
        query = select(security_events).where(*conditions)
        if cursor is not None:
            created_at, row_id = cursor
            query = query.where(
                or_(
                    security_events.c.created_at < created_at,
                    and_(
                        security_events.c.created_at == created_at,
                        security_events.c.id < row_id,
                    ),
                )
            )
        rows = list(
            (
                await session.execute(
                    query.order_by(desc(security_events.c.created_at), desc(security_events.c.id)).limit(_BATCH_SIZE)
                )
            ).mappings()
        )
        if not rows:
            return
        for row in rows:
            writer.writerow(_csv_row(row))
        yield _drain(buffer)
        if len(rows) < _BATCH_SIZE:
            return
        cursor = (rows[-1]["created_at"], rows[-1]["id"])


def _csv_row(row: RowMapping) -> list[str]:
    """Render one security-event mapping as a list of CSV cell values."""
    return [
        _format_timestamp(row["created_at"]),
        _text(row["module"]),
        _text(row["event_type"]),
        _text(row["severity"]),
        _text(row["actor_user_id"]),
        _text(row["actor_label"]),
        _text(row["identifier_hash"]),
        _text(row["client_ip"]),
        _text(row["source_port"]),
        _text(row["request_id"]),
        _text(row["user_agent"]),
        _format_metadata(row["metadata"]),
    ]


def _format_timestamp(value: datetime | None) -> str:
    """Return a ``YYYY-MM-DD HH:MM:SS`` UTC representation of a stored timestamp."""
    if value is None:
        return ""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _format_metadata(value: object) -> str:
    """Return the JSON metadata payload as a compact single-line string."""
    if not value:
        return ""
    return _text(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _text(value: object) -> str:
    """Return a spreadsheet-safe cell value.

    A leading ``=``, ``+``, ``-``, or ``@`` is prefixed with an apostrophe so a
    spreadsheet treats attacker-influenced fields (user agents, metadata) as
    text rather than as a formula.
    """
    if value is None:
        return ""
    text = str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    if text[:1] in {"=", "+", "-", "@"}:
        return f"'{text}"
    return text


def _drain(buffer: io.StringIO) -> str:
    """Return and clear the CSV writer's accumulated output."""
    chunk = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return chunk
