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
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

TRIGRAM_SIMILARITY_THRESHOLD = 0.3
MIN_FUZZY_QUERY_LENGTH = 3
LIKE_ESCAPE = "\\"
# Per-term substring matching is one ILIKE per term, so an unbounded term count
# would let a single query build an unbounded predicate.
MAX_SUBSTRING_TERMS = 8
# pg_trgm needs three consecutive word characters to extract a trigram from an
# unanchored ``%pattern%``, so a shorter term can only be answered by reading
# every row.
MIN_TRIGRAM_TERM_LENGTH = 3

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


def substring_terms(query: str) -> list[str]:
    """Return the whitespace-separated terms to match independently as substrings.

    Full-text matching compares whole lexemes, so a term the user has not
    finished typing (``"2024 Loca"``) matches nothing, and whole-query substring
    matching only helps while the typed text stays contiguous in the stored
    value. Matching each term as its own substring covers both: terms may be
    partial, appear in any order, and match inside a word.

    Args:
        query: Raw user-supplied search text.

    Returns:
        At most ``MAX_SUBSTRING_TERMS`` terms, in the order typed.
    """
    return query.split()[:MAX_SUBSTRING_TERMS]


def _is_trigram_word_char(char: str) -> bool:
    """Return whether pg_trgm counts a character as word text.

    pg_trgm extracts trigrams from letters and decimal digits and treats
    everything else as a separator. The Unicode category is the test rather than
    ``str.isalnum``, which is broader: it accepts characters such as ``²``
    (category ``No``) that PostgreSQL discards, and treating those as searchable
    is what would reintroduce a sequential scan.
    """
    category = unicodedata.category(char)
    return category.startswith("L") or category == "Nd"


def term_has_indexable_trigram(term: str) -> bool:
    """Return whether a term can be served from a ``gin_trgm_ops`` index.

    Character count is not sufficient: ``"---"``, ``"²²²"``, and an emoji run are
    each three characters that yield no trigram at all, so PostgreSQL answers
    them -- and the ``%`` similarity operator with them -- by sequential scan.
    The test is therefore three *word* characters, which the separators in a
    term need not be adjacent to: every ``%pattern%`` probed with three of them
    (``"gam"``, ``"a-bc"``, ``"a-b-c"``, ``"a.b.c"``) plans as a bitmap index
    scan, and every pattern with fewer (``"ga"``, ``"a-"``, ``"---"``) plans as
    a sequential scan.

    The rule is deliberately pessimistic where PostgreSQL is more permissive
    still: a separator can make ``"J-P"`` indexable on two word characters. An
    optimistic answer costs a table scan, while a pessimistic one only declines
    to search -- and declining matches what the caller is told, that a term
    needs three letters or digits.

    Args:
        term: One whitespace-separated term of a user query.

    Returns:
        True when the term carries at least ``MIN_TRIGRAM_TERM_LENGTH`` word
        characters.
    """
    word_characters = sum(1 for char in term if _is_trigram_word_char(char))
    return word_characters >= MIN_TRIGRAM_TERM_LENGTH


def query_is_trigram_searchable(query: str) -> bool:
    """Return whether every term of a query can be answered from a trigram index.

    Every term must qualify, not merely one: the terms are AND-ed, so a term that
    cannot be index-matched would have to be evaluated by reading the rows the
    other terms selected.

    Args:
        query: Raw user-supplied search text.

    Returns:
        True when the query has at least one term and all of them qualify.
    """
    terms = substring_terms(query)
    return bool(terms) and all(term_has_indexable_trigram(term) for term in terms)


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
