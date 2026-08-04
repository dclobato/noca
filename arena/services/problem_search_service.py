#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared search expressions for Arena problem lists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from typing import cast as type_cast

from sqlalchemy import (
    String,
    Text,
    and_,
    case,
    cast,
    false,
    func,
    literal,
    literal_column,
    or_,
    select,
    union,
)
from sqlalchemy.dialects.postgresql import REGCONFIG, TSVECTOR
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from arena.models.arena_problems import ArenaProblem
from arena.services.text_search_primitives import (
    LIKE_ESCAPE as _LIKE_ESCAPE,
)
from arena.services.text_search_primitives import (
    MIN_FUZZY_QUERY_LENGTH as _MIN_FUZZY_QUERY_LENGTH,
)
from arena.services.text_search_primitives import (
    apply_trigram_threshold,
    escaped_substring_pattern,
    uses_websearch_syntax,
)
from shared.db_schema.arena import arena_users as _users_table
from shared.enumerations import StatementLanguage

_MAX_ARENA_NUMBER = 2_147_483_647

type ProblemSuggestionField = Literal["author", "source"]

_SEARCH_VECTOR_SQL = """
setweight(
    to_tsvector(
        CASE arena_problems.statement_language
            WHEN 'pt' THEN 'portuguese'::regconfig
            WHEN 'en' THEN 'english'::regconfig
            WHEN 'es' THEN 'spanish'::regconfig
            ELSE 'simple'::regconfig
        END,
        coalesce(arena_problems.title, '')
    ),
    'A'
)
|| setweight(
    to_tsvector(
        CASE arena_problems.statement_language
            WHEN 'pt' THEN 'portuguese'::regconfig
            WHEN 'en' THEN 'english'::regconfig
            WHEN 'es' THEN 'spanish'::regconfig
            ELSE 'simple'::regconfig
        END,
        coalesce(arena_problems.source, '') || ' ' || coalesce(arena_problems.author, '')
    ),
    'B'
)
|| setweight(
    to_tsvector(
        CASE arena_problems.statement_language
            WHEN 'pt' THEN 'portuguese'::regconfig
            WHEN 'en' THEN 'english'::regconfig
            WHEN 'es' THEN 'spanish'::regconfig
            ELSE 'simple'::regconfig
        END,
        coalesce(arena_problems.problem_statement, '')
    ),
    'D'
)
""".strip()

_FIELD_SEARCH_VECTOR_SQL_TEMPLATE = """
to_tsvector(
    CASE arena_problems.statement_language
        WHEN 'pt' THEN 'portuguese'::regconfig
        WHEN 'en' THEN 'english'::regconfig
        WHEN 'es' THEN 'spanish'::regconfig
        ELSE 'simple'::regconfig
    END,
    coalesce(arena_problems.{field}, '')
)
""".strip()

_FIELD_SEARCH_VECTOR_SQL: dict[ProblemSuggestionField, str] = {
    "author": _FIELD_SEARCH_VECTOR_SQL_TEMPLATE.format(field="author"),
    "source": _FIELD_SEARCH_VECTOR_SQL_TEMPLATE.format(field="source"),
}


@dataclass(frozen=True)
class ProblemSearchExpressions:
    """Predicate and ranking expressions for one normalized search query."""

    predicate: ColumnElement[bool]
    exact_number_match: ColumnElement[bool]
    full_text_match: ColumnElement[bool]
    full_text_rank: ColumnElement[Any]
    trigram_rank: ColumnElement[Any]


@dataclass(frozen=True)
class ProblemSuggestionSearchExpressions:
    """Predicate and ranking expressions for one author/source suggestion query."""

    predicate: ColumnElement[bool]
    full_text_rank: ColumnElement[Any]
    trigram_rank: ColumnElement[Any]


def _resolved_author() -> ColumnElement[str | None]:
    """Return the owner-backed or free-text problem author expression."""
    return case(
        (ArenaProblem.author_is_owner.is_(True), _users_table.c.nome),
        else_=ArenaProblem.author,
    )


def _exact_number_match(query: str) -> ColumnElement[bool]:
    """Return an exact Arena-number predicate when the query is numeric.

    ``str.isdigit`` is true for characters ``int`` rejects (superscripts such as
    ``²`` and circled digits such as ``⑦``), so the ASCII check must come first.
    The upper bound keeps the comparison inside ``arena_number``'s ``int4``
    domain; a larger value would fail at parameter encoding rather than simply
    matching nothing.
    """
    if not (query.isascii() and query.isdigit()):
        return false()
    number = int(query)
    if not 1 <= number <= _MAX_ARENA_NUMBER:
        return false()
    return ArenaProblem.arena_number == number


def _portable_search(query: str) -> ProblemSearchExpressions:
    """Build the SQLite-compatible behavior used by unit tests."""
    pattern = escaped_substring_pattern(query)
    resolved_author = _resolved_author()
    predicate = or_(
        cast(ArenaProblem.arena_number, String).ilike(pattern, escape=_LIKE_ESCAPE),
        ArenaProblem.title.ilike(pattern, escape=_LIKE_ESCAPE),
        ArenaProblem.problem_statement.ilike(pattern, escape=_LIKE_ESCAPE),
        ArenaProblem.source.ilike(pattern, escape=_LIKE_ESCAPE),
        resolved_author.ilike(pattern, escape=_LIKE_ESCAPE),
    )
    return ProblemSearchExpressions(
        predicate=predicate,
        exact_number_match=_exact_number_match(query),
        full_text_match=false(),
        full_text_rank=literal(0.0),
        trigram_rank=literal(0.0),
    )


def _text_query(config: str, query: str) -> ColumnElement[Any]:
    """Parse a web-style query with a fixed PostgreSQL text configuration."""
    return func.websearch_to_tsquery(cast(literal(config), REGCONFIG), query)


def _literal_text_query(config: str, query: str) -> ColumnElement[Any]:
    """Parse literal autocomplete text with a fixed PostgreSQL text configuration."""
    return func.plainto_tsquery(cast(literal(config), REGCONFIG), query)


def _text_queries(
    query: str,
    *,
    literal_query: bool = False,
) -> dict[StatementLanguage | None, ColumnElement[Any]]:
    """Return text queries for every statement-language index configuration."""
    query_builder = _literal_text_query if literal_query else _text_query
    return {
        StatementLanguage.PT: query_builder("portuguese", query),
        StatementLanguage.EN: query_builder("english", query),
        StatementLanguage.ES: query_builder("spanish", query),
        None: query_builder("simple", query),
    }


def _search_vector(table_name: str = "arena_problems") -> ColumnElement[Any]:
    """Return the weighted FTS vector used by the indexed problem-search candidate path."""
    return literal_column(
        _SEARCH_VECTOR_SQL.replace("arena_problems.", f"{table_name}."),
        TSVECTOR(),
    )


def _field_search_vector(
    field: ProblemSuggestionField,
    table_name: str = "arena_problems",
) -> ColumnElement[Any]:
    """Return the requested field's language-aware PostgreSQL FTS vector."""
    field_sql = _FIELD_SEARCH_VECTOR_SQL[field]
    return literal_column(field_sql.replace("arena_problems.", f"{table_name}."), TSVECTOR())


def _full_text_match(
    search_vector: ColumnElement[Any],
    text_queries: dict[StatementLanguage | None, ColumnElement[Any]],
) -> ColumnElement[bool]:
    """Return the row-language-aware match predicate for an FTS vector."""
    return or_(
        and_(
            ArenaProblem.statement_language == StatementLanguage.PT,
            search_vector.bool_op("@@")(text_queries[StatementLanguage.PT]),
        ),
        and_(
            ArenaProblem.statement_language == StatementLanguage.EN,
            search_vector.bool_op("@@")(text_queries[StatementLanguage.EN]),
        ),
        and_(
            ArenaProblem.statement_language == StatementLanguage.ES,
            search_vector.bool_op("@@")(text_queries[StatementLanguage.ES]),
        ),
        and_(
            ArenaProblem.statement_language.is_(None),
            search_vector.bool_op("@@")(text_queries[None]),
        ),
    )


def _full_text_rank(
    search_vector: ColumnElement[Any],
    text_queries: dict[StatementLanguage | None, ColumnElement[Any]],
) -> ColumnElement[Any]:
    """Return the row-language-aware rank for an FTS vector."""
    return case(
        (
            ArenaProblem.statement_language == StatementLanguage.PT,
            func.ts_rank_cd(search_vector, text_queries[StatementLanguage.PT], 32),
        ),
        (
            ArenaProblem.statement_language == StatementLanguage.EN,
            func.ts_rank_cd(search_vector, text_queries[StatementLanguage.EN], 32),
        ),
        (
            ArenaProblem.statement_language == StatementLanguage.ES,
            func.ts_rank_cd(search_vector, text_queries[StatementLanguage.ES], 32),
        ),
        else_=func.ts_rank_cd(search_vector, text_queries[None], 32),
    )


def _field_column(field: ProblemSuggestionField) -> ColumnElement[str | None]:
    """Return the stored free-text column for one autocomplete field."""
    if field == "author":
        return type_cast(ColumnElement[str | None], ArenaProblem.author)
    return type_cast(ColumnElement[str | None], ArenaProblem.source)


def _field_candidate_conditions(
    field: ProblemSuggestionField,
    value: Any,
    author_is_owner: Any,
) -> list[ColumnElement[bool]]:
    """Return the stored-value constraints shared by suggestion candidate branches."""
    conditions = [value.is_not(None)]
    if field == "author":
        conditions.append(author_is_owner.is_(False))
    return conditions


def _full_text_candidate_branches(
    text_queries: dict[StatementLanguage | None, ColumnElement[Any]],
    *,
    field: ProblemSuggestionField | None = None,
) -> list[Any]:
    """Return independent composite-index FTS candidate branches.

    When ``field`` is given, each composite FTS candidate also confirms the
    requested stored field. That avoids title, statement, or the other
    suggestion field producing an irrelevant value.
    """
    problems = ArenaProblem.__table__.alias("search_problem")
    search_vector = _search_vector("search_problem")
    conditions_by_query: list[tuple[ColumnElement[bool], ColumnElement[Any]]] = [
        (problems.c.statement_language == language, text_query)
        for language, text_query in text_queries.items()
        if language is not None
    ]
    conditions_by_query.append((problems.c.statement_language.is_(None), text_queries[None]))

    field_vector = _field_search_vector(field, "search_problem") if field is not None else None
    field_value = problems.c[field] if field is not None else None
    field_conditions = (
        _field_candidate_conditions(field, field_value, problems.c.author_is_owner)
        if field is not None and field_value is not None
        else []
    )
    return [
        select(problems.c.id).where(
            language_condition,
            search_vector.bool_op("@@")(text_query),
            *field_conditions,
            *([field_vector.bool_op("@@")(text_query)] if field_vector is not None else []),
        )
        for language_condition, text_query in conditions_by_query
    ]


def _candidate_problem_ids(
    query: str,
    text_queries: dict[StatementLanguage | None, ColumnElement[Any]],
) -> Any:
    """Return independently indexable PostgreSQL candidate-ID branches."""
    problems = ArenaProblem.__table__.alias("search_problem")
    users = _users_table.alias("search_owner")
    candidates = _full_text_candidate_branches(text_queries)

    if uses_websearch_syntax(query):
        return union(*candidates)

    pattern = escaped_substring_pattern(query)
    candidates.extend(
        [
            select(problems.c.id).where(cast(problems.c.arena_number, Text).ilike(pattern, escape=_LIKE_ESCAPE)),
            select(problems.c.id).where(problems.c.title.ilike(pattern, escape=_LIKE_ESCAPE)),
            select(problems.c.id).where(problems.c.problem_statement.ilike(pattern, escape=_LIKE_ESCAPE)),
            select(problems.c.id).where(problems.c.source.ilike(pattern, escape=_LIKE_ESCAPE)),
            select(problems.c.id).where(
                problems.c.author_is_owner.is_(False),
                problems.c.author.ilike(pattern, escape=_LIKE_ESCAPE),
            ),
            select(problems.c.id)
            .join(users, problems.c.owner_id == users.c.id)
            .where(
                problems.c.author_is_owner.is_(True),
                users.c.nome.ilike(pattern, escape=_LIKE_ESCAPE),
            ),
        ]
    )
    if len(query) >= _MIN_FUZZY_QUERY_LENGTH:
        candidates.extend(
            [
                select(problems.c.id).where(problems.c.title.bool_op("%")(query)),
                select(problems.c.id).where(problems.c.source.bool_op("%")(query)),
                select(problems.c.id).where(
                    problems.c.author_is_owner.is_(False),
                    problems.c.author.bool_op("%")(query),
                ),
                select(problems.c.id)
                .join(users, problems.c.owner_id == users.c.id)
                .where(
                    problems.c.author_is_owner.is_(True),
                    users.c.nome.bool_op("%")(query),
                ),
            ]
        )
    return union(*candidates)


def _postgres_search(query: str) -> ProblemSearchExpressions:
    """Build PostgreSQL full-text, substring, fuzzy, and rank expressions."""
    search_vector = _search_vector()
    text_queries = _text_queries(query)
    full_text_match = _full_text_match(search_vector, text_queries)
    resolved_author = _resolved_author()

    trigram_rank = func.greatest(
        func.similarity(ArenaProblem.title, query),
        func.coalesce(func.similarity(resolved_author, query) * 0.7, 0.0),
        func.coalesce(func.similarity(ArenaProblem.source, query) * 0.6, 0.0),
        func.word_similarity(query, ArenaProblem.problem_statement) * 0.2,
    )
    return ProblemSearchExpressions(
        predicate=ArenaProblem.id.in_(_candidate_problem_ids(query, text_queries)),
        exact_number_match=_exact_number_match(query),
        full_text_match=full_text_match,
        full_text_rank=_full_text_rank(search_vector, text_queries),
        trigram_rank=trigram_rank,
    )


def _portable_suggestion_search(
    field: ProblemSuggestionField,
    query: str,
) -> ProblemSuggestionSearchExpressions:
    """Build SQLite-compatible field-only literal substring suggestion matching."""
    value = _field_column(field)
    predicate = and_(
        *_field_candidate_conditions(field, value, ArenaProblem.author_is_owner),
        value.ilike(escaped_substring_pattern(query), escape=_LIKE_ESCAPE),
    )
    return ProblemSuggestionSearchExpressions(
        predicate=predicate,
        full_text_rank=literal(0.0),
        trigram_rank=literal(0.0),
    )


def _suggestion_candidate_problem_ids(
    field: ProblemSuggestionField,
    query: str,
    text_queries: dict[StatementLanguage | None, ColumnElement[Any]],
) -> Any:
    """Return independent FTS, literal, and fuzzy candidate branches for one stored field."""
    candidates = _full_text_candidate_branches(text_queries, field=field)
    problems = ArenaProblem.__table__.alias("suggestion_problem")
    value = problems.c[field]
    field_conditions = _field_candidate_conditions(field, value, problems.c.author_is_owner)
    pattern = escaped_substring_pattern(query)
    candidates.append(
        select(problems.c.id).where(
            *field_conditions,
            value.ilike(pattern, escape=_LIKE_ESCAPE),
        )
    )
    if len(query) >= _MIN_FUZZY_QUERY_LENGTH:
        candidates.append(
            select(problems.c.id).where(
                *field_conditions,
                value.bool_op("%")(query),
            )
        )
    return union(*candidates)


def _postgres_suggestion_search(
    field: ProblemSuggestionField,
    query: str,
) -> ProblemSuggestionSearchExpressions:
    """Build PostgreSQL field-specific FTS, substring, fuzzy, and rank expressions."""
    value = _field_column(field)
    text_queries = _text_queries(query, literal_query=True)
    field_vector = _field_search_vector(field)
    return ProblemSuggestionSearchExpressions(
        predicate=ArenaProblem.id.in_(_suggestion_candidate_problem_ids(field, query, text_queries)),
        full_text_rank=_full_text_rank(field_vector, text_queries),
        trigram_rank=(func.similarity(value, query) if len(query) >= _MIN_FUZZY_QUERY_LENGTH else literal(0.0)),
    )


async def prepare_problem_search(session: AsyncSession, query: str) -> ProblemSearchExpressions:
    """Build search expressions and configure deterministic trigram matching."""
    normalized_query = query.strip()
    if session.get_bind().dialect.name != "postgresql":
        return _portable_search(normalized_query)
    if len(normalized_query) >= _MIN_FUZZY_QUERY_LENGTH and not uses_websearch_syntax(normalized_query):
        await apply_trigram_threshold(session)
    return _postgres_search(normalized_query)


async def prepare_problem_suggestion_search(
    session: AsyncSession,
    field: ProblemSuggestionField,
    query: str,
) -> ProblemSuggestionSearchExpressions:
    """Build field-specific autocomplete expressions with the session-local trigram threshold."""
    normalized_query = query.strip()
    if session.get_bind().dialect.name != "postgresql":
        return _portable_suggestion_search(field, normalized_query)
    if len(normalized_query) >= _MIN_FUZZY_QUERY_LENGTH:
        await apply_trigram_threshold(session)
    return _postgres_suggestion_search(field, normalized_query)
