#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Bounded request-parameter types for NOCA HTTP applications.

Python integers are unbounded, so an ``int`` route parameter accepts a value far
larger than the PostgreSQL column it is compared against.  asyncpg then raises
``DataError: value out of int32 range`` at query time, which surfaces to the
caller as a 503 and writes a full SQL statement to the log -- a trivially
triggerable error path, and a misleading one, since nothing is actually
unavailable.

Bounding the parameter instead turns that into an ordinary rejected request that
never reaches the database.  Use these aliases for any parameter compared with an
integer column rather than repeating the bounds per route.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query
from starlette.convertors import Convertor, register_url_convertor

# PostgreSQL ``integer`` is a signed 32-bit type.  The shared schema uses it for
# essentially every identifier (the handful of BigInteger columns are login- and
# rating-history rows and worker-control bookkeeping, none of which is addressed
# by a route parameter).  A route that does address a BigInteger column needs its
# own wider bound rather than this alias.
PG_INT32_MAX = 2_147_483_647

# Upper bound for a page number.  At the page sizes used here this keeps the
# computed SQL OFFSET orders of magnitude inside int32, so a hostile page number
# is rejected before it can reach a query.
MAX_PAGE = 1_000_000

# Widest value the convertor below will parse.  Ten digits comfortably covers the
# int32 range while staying far below CPython's 4300-digit limit for
# int(str) conversion.
_MAX_PATH_DIGITS = 10


class BoundedIntConvertor(Convertor[int]):
    """A path convertor for integer identifiers that cannot raise while matching.

    Starlette's built-in ``int`` convertor matches ``[0-9]+`` and then calls
    ``int(value)``.  CPython refuses to parse a decimal string longer than
    ``sys.get_int_max_str_digits()`` (4300 by default), so a long enough numeric
    path raises ``ValueError`` inside ``Route.matches()`` -- during routing, before
    any dependency or handler runs.  That escapes the HTTP exception handlers
    entirely and becomes a 500 with the full path and a traceback in the log,
    reachable without authenticating.

    Bounding the digit count in the *regex* means an oversized path simply fails
    to match the route, so it is an ordinary 404 and ``convert`` is never reached
    with anything CPython will not parse.  A value that is well-formed but larger
    than the column is still rejected by the ``le`` bound on ``DbId``.
    """

    regex = f"[0-9]{{1,{_MAX_PATH_DIGITS}}}"

    def convert(self, value: str) -> int:
        """Return the integer for a path segment already limited by the regex."""
        return int(value)

    def to_string(self, value: int) -> str:
        """Render an integer back into a URL path segment."""
        return str(value)


# Registered at import time.  Route modules import `DbId` from here, so this runs
# before any route using `{name:dbid}` is declared.
register_url_convertor("dbid", BoundedIntConvertor())

DbId = Annotated[int, Path(ge=1, le=PG_INT32_MAX)]
"""A path parameter naming a database row by its integer identifier.

Pair this with the ``dbid`` path convertor (``{arena_number:dbid}``) rather than
the built-in ``int`` one, so an oversized path segment is a 404 at match time
instead of a 500.
"""

DbIdQuery = Annotated[int, Query(ge=1, le=PG_INT32_MAX)]
"""A query parameter naming a database row by its integer identifier."""

PageNumber = Annotated[int, Query(ge=1, le=MAX_PAGE)]
"""A one-based page number from the query string."""
