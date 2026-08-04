#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Indexed candidate-ID search over Arena users and affiliations.

Callers get a **candidate-ID selectable** against the base table and apply it as
``<their own query>.c.id.in_(...)``. That shape exists because the ranking pages
filter a CTE carrying a ``RANK()`` window function: a ``WHERE`` on the output of
a windowed CTE cannot be pushed down to the base table, so a predicate written
against the CTE's columns can never use an index. The same selectable serves
callers that query ``arena_users`` directly, such as the class-membership
student autocomplete.

Each branch of that selectable constrains exactly one column so PostgreSQL can
serve it from one index and combine the branches with a BitmapOr, mirroring
``problem_search_service._candidate_problem_ids``.

Relevance ordering is **opt-in**, through ``user_relevance_ordering``. The
ranking pages must not use it: they order by ``global_rank``, and ordering by
relevance would break the "rank #47 remains #47" invariant the ranking service
documents. A short autocomplete list needs the opposite — it truncates to a
handful of rows, so the best match has to come first or it is simply not shown.

The candidate set deliberately omits every eligibility predicate (``ativo`` /
``email_confirmado`` / ``ranking_visible`` / role for users,
``exclude_from_ranking`` for affiliations). Each caller already applies the ones
it needs, so the join discards any ineligible ID the search matched;
re-applying them here would only turn a clean single-index bitmap scan into a
heap recheck on low-selectivity booleans.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, case, cast, func, literal, literal_column, or_, select, union
from sqlalchemy.dialects.postgresql import REGCONFIG, TSVECTOR
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CompoundSelect

from arena.services.text_search_primitives import (
    LIKE_ESCAPE,
    MIN_FUZZY_QUERY_LENGTH,
    apply_trigram_threshold,
    escaped_substring_pattern,
    uses_websearch_syntax,
)
from shared.db_schema.arena import arena_affiliations, arena_users

type CandidateIds = Select[Any] | CompoundSelect[Any]

_USER_ALIAS = "search_arena_user"
_AFFILIATION_ALIAS = "search_arena_affiliation"

# The ``simple`` configuration is deliberate: names are proper nouns, so stemming
# and stopword removal would only lose information ("Santos" must not stem, "de"
# must not vanish from "Universidade de Brasilia").
#
# Both strings must stay byte-identical to the expression indexes created by
# migration 202608030001, or PostgreSQL will not match the expression and the
# index goes unused. ``tests/arena/test_identity_search_service.py`` asserts that.
# The explicit ``'simple'::regconfig`` cast is required: the one-argument
# ``to_tsvector`` reads ``default_text_search_config`` and is only STABLE, which
# PostgreSQL rejects in an index expression.
_USER_NAME_VECTOR_SQL = "to_tsvector('simple'::regconfig, coalesce(arena_users.nome, ''))"
_AFFILIATION_NAME_VECTOR_SQL = "to_tsvector('simple'::regconfig, coalesce(arena_affiliations.name, ''))"


def _text_query(query: str) -> ColumnElement[Any]:
    """Parse a web-style query with the ``simple`` text configuration."""
    return func.websearch_to_tsquery(cast(literal("simple"), REGCONFIG), query)


def _user_name_vector(table_name: str) -> ColumnElement[Any]:
    """Return the Arena user name FTS vector bound to one table name or alias."""
    return literal_column(_USER_NAME_VECTOR_SQL.replace("arena_users.", f"{table_name}."), TSVECTOR())


def _affiliation_name_vector(table_name: str) -> ColumnElement[Any]:
    """Return the affiliation name FTS vector bound to one table name or alias."""
    return literal_column(
        _AFFILIATION_NAME_VECTOR_SQL.replace("arena_affiliations.", f"{table_name}."),
        TSVECTOR(),
    )


def _combined(candidates: list[Select[Any]]) -> CandidateIds:
    """Combine candidate branches, avoiding a single-branch ``UNION``.

    Unlike problem search, which always contributes one FTS branch per statement
    language, both ranking entities degrade to exactly one branch when the query
    carries websearch operators.

    Args:
        candidates: One or more single-column candidate-ID selects.

    Returns:
        The lone select, or the union of all branches.
    """
    if len(candidates) == 1:
        return candidates[0]
    return union(*candidates)


def _postgres_user_candidates(query: str) -> CandidateIds:
    """Return independently indexable Arena user candidate-ID branches.

    Args:
        query: Normalized (stripped) search text.

    Returns:
        Candidate-ID selectable covering full-text name matching plus, for
        queries without websearch operators, substring matching on name and
        email and fuzzy matching on name.
    """
    users = arena_users.alias(_USER_ALIAS)
    candidates: list[Select[Any]] = [
        select(users.c.id).where(_user_name_vector(_USER_ALIAS).bool_op("@@")(_text_query(query))),
    ]
    if uses_websearch_syntax(query):
        return _combined(candidates)

    pattern = escaped_substring_pattern(query)
    candidates.extend(
        [
            select(users.c.id).where(users.c.nome.ilike(pattern, escape=LIKE_ESCAPE)),
            select(users.c.id).where(users.c.email_normalizado.ilike(pattern, escape=LIKE_ESCAPE)),
        ]
    )
    if len(query) >= MIN_FUZZY_QUERY_LENGTH:
        # Names tolerate typos; an email address is an exact identifier, so a
        # fuzzy email match would be noise rather than a find.
        candidates.append(select(users.c.id).where(users.c.nome.bool_op("%")(query)))
    return _combined(candidates)


def _postgres_affiliation_candidates(query: str) -> CandidateIds:
    """Return independently indexable Arena affiliation candidate-ID branches.

    Args:
        query: Normalized (stripped) search text.

    Returns:
        Candidate-ID selectable covering full-text name matching plus, for
        queries without websearch operators, substring and fuzzy name matching.
    """
    affiliations = arena_affiliations.alias(_AFFILIATION_ALIAS)
    candidates: list[Select[Any]] = [
        select(affiliations.c.id).where(_affiliation_name_vector(_AFFILIATION_ALIAS).bool_op("@@")(_text_query(query))),
    ]
    if uses_websearch_syntax(query):
        return _combined(candidates)

    pattern = escaped_substring_pattern(query)
    candidates.append(select(affiliations.c.id).where(affiliations.c.name.ilike(pattern, escape=LIKE_ESCAPE)))
    if len(query) >= MIN_FUZZY_QUERY_LENGTH:
        candidates.append(select(affiliations.c.id).where(affiliations.c.name.bool_op("%")(query)))
    return _combined(candidates)


def _portable_user_candidates(query: str) -> CandidateIds:
    """Return the SQLite-compatible Arena user candidate IDs used by unit tests."""
    users = arena_users.alias(_USER_ALIAS)
    pattern = escaped_substring_pattern(query)
    return select(users.c.id).where(
        or_(
            users.c.nome.ilike(pattern, escape=LIKE_ESCAPE),
            users.c.email_normalizado.ilike(pattern, escape=LIKE_ESCAPE),
        )
    )


def _portable_affiliation_candidates(query: str) -> CandidateIds:
    """Return the SQLite-compatible affiliation candidate IDs used by unit tests."""
    affiliations = arena_affiliations.alias(_AFFILIATION_ALIAS)
    return select(affiliations.c.id).where(
        affiliations.c.name.ilike(escaped_substring_pattern(query), escape=LIKE_ESCAPE)
    )


async def prepare_user_search(session: AsyncSession, query: str) -> CandidateIds:
    """Build the Arena user candidate-ID selectable for one search query.

    Args:
        session: Active async database session.
        query: Raw user-supplied search text.

    Returns:
        A selectable of matching ``arena_users.id`` values, for use as
        ``<outer query>.c.id.in_(...)``.
    """
    normalized_query = query.strip()
    if session.get_bind().dialect.name != "postgresql":
        return _portable_user_candidates(normalized_query)
    if len(normalized_query) >= MIN_FUZZY_QUERY_LENGTH and not uses_websearch_syntax(normalized_query):
        await apply_trigram_threshold(session)
    return _postgres_user_candidates(normalized_query)


async def prepare_affiliation_search(session: AsyncSession, query: str) -> CandidateIds:
    """Build the affiliation candidate-ID selectable for one search query.

    Args:
        session: Active async database session.
        query: Raw user-supplied search text.

    Returns:
        A selectable of matching ``arena_affiliations.id`` values, for use as
        ``<outer query>.c.id.in_(...)``.
    """
    normalized_query = query.strip()
    if session.get_bind().dialect.name != "postgresql":
        return _portable_affiliation_candidates(normalized_query)
    if len(normalized_query) >= MIN_FUZZY_QUERY_LENGTH and not uses_websearch_syntax(normalized_query):
        await apply_trigram_threshold(session)
    return _postgres_affiliation_candidates(normalized_query)


def user_relevance_ordering(session: AsyncSession, query: str) -> list[ColumnElement[Any]]:
    """Return ``ORDER BY`` terms that put the best Arena user matches first.

    Only callers that truncate their result set need this. Fuzzy matching widens
    the candidate set with rows that contain no literal trace of the query, so a
    purely alphabetical order can push the row the user actually typed past the
    cut-off and out of sight. Ordering is therefore: rows containing the query
    literally, then descending name similarity, then name for determinism.

    The ranking pages must **not** use this — they order by ``global_rank``.

    Args:
        session: Active async database session.
        query: Raw user-supplied search text.

    Returns:
        Ordering expressions over ``arena_users``, to be spliced ahead of the
        caller's own tie-breakers. Empty for a blank query, and name-only on
        non-PostgreSQL dialects, which have neither ``similarity`` nor a
        meaningful relevance notion here.
    """
    normalized_query = query.strip()
    if not normalized_query:
        return []
    if session.get_bind().dialect.name != "postgresql":
        return [arena_users.c.nome.asc()]

    pattern = escaped_substring_pattern(normalized_query)
    literal_hit = case(
        (
            or_(
                arena_users.c.nome.ilike(pattern, escape=LIKE_ESCAPE),
                arena_users.c.email_normalizado.ilike(pattern, escape=LIKE_ESCAPE),
            ),
            1,
        ),
        else_=0,
    )
    return [
        literal_hit.desc(),
        func.similarity(arena_users.c.nome, normalized_query).desc(),
        arena_users.c.nome.asc(),
    ]
