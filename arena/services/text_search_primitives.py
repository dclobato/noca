#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Primitives shared by every Arena PostgreSQL text-search service.

This module deliberately holds only the rules that must stay identical across
search paths: how user wildcards are neutralized, when a query carries
``websearch_to_tsquery`` operator semantics, and the session-local ``pg_trgm``
similarity threshold. Divergent escaping or operator detection between two
search services is a correctness hazard, so both
``problem_search_service`` and ``identity_search_service`` import them from here.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

TRIGRAM_SIMILARITY_THRESHOLD = 0.3
MIN_FUZZY_QUERY_LENGTH = 3
LIKE_ESCAPE = "\\"

# These tokens have PostgreSQL websearch semantics even when they occur in
# natural-language text (for example, "true or false" or "OR gate").
_WEBSEARCH_OPERATOR = re.compile(r"(?:^|\s)(?:OR\s+|-\S)", re.IGNORECASE)


def escaped_substring_pattern(query: str) -> str:
    """Return an ILIKE pattern that treats user wildcard characters literally.

    Args:
        query: Raw user-supplied search text.

    Returns:
        A ``%``-wrapped pattern in which the user's own ``%``, ``_``, and
        backslash characters match literally. The backslash is escaped first so
        the escapes added afterwards are not themselves re-escaped.
    """
    escaped = query.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    escaped = escaped.replace("%", f"{LIKE_ESCAPE}%").replace("_", f"{LIKE_ESCAPE}_")
    return f"%{escaped}%"


def uses_websearch_syntax(query: str) -> bool:
    """Return whether fallback matching would undermine query operators.

    A quoted phrase, an ``OR``, or a leading ``-`` negation means the user asked
    for precise tsquery semantics. Substring and fuzzy fallback branches must
    then be suppressed, or they would resurrect exactly the rows the operators
    were meant to exclude.

    Args:
        query: Raw user-supplied search text.

    Returns:
        True when the query carries websearch operator semantics.
    """
    return '"' in query or _WEBSEARCH_OPERATOR.search(query) is not None


async def apply_trigram_threshold(session: AsyncSession) -> None:
    """Set the transaction-local ``pg_trgm`` similarity threshold.

    The ``%`` operator consults ``pg_trgm.similarity_threshold``, which is a
    server-configurable GUC. Pinning it per transaction (``SET LOCAL``) keeps
    fuzzy matching deterministic regardless of server configuration, without
    leaking the setting to the next transaction on the pooled connection.

    Args:
        session: Active async database session bound to PostgreSQL.
    """
    await session.execute(
        select(
            func.set_config(
                "pg_trgm.similarity_threshold",
                str(TRIGRAM_SIMILARITY_THRESHOLD),
                True,
            )
        )
    )
