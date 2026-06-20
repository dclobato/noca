#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for aiassistant.db.queries — focused on get_problem_data."""

from __future__ import annotations

from datetime import date

import pytest

from aiassistant.db.queries import ProblemData, get_problem_data, get_user_prefered_language
from shared.db_schema import arena_problems, arena_users
from shared.enumerations import ArenaRole


async def _insert_user(conn: object, user_id: str) -> None:
    """Insert a minimal arena_users row."""
    from sqlalchemy.ext.asyncio import AsyncConnection

    db_conn: AsyncConnection = conn  # type: ignore[assignment]
    await db_conn.execute(
        arena_users.insert().values(
            id=user_id,
            nome="Query Test User",
            dta_nascimento=date(1995, 6, 15),
            email_normalizado=f"{user_id}@test.example.com",
            password_hash="hash",
            role=ArenaRole.ARENA_USER.value,
            _ai_api_key=None,
        )
    )


async def _insert_user_with_prefered_language(conn: object, user_id: str, prefered_language: str) -> None:
    """Insert a minimal arena_users row with a preferred locale."""
    from sqlalchemy.ext.asyncio import AsyncConnection

    db_conn: AsyncConnection = conn  # type: ignore[assignment]
    await db_conn.execute(
        arena_users.insert().values(
            id=user_id,
            nome="Query Test User",
            dta_nascimento=date(1995, 6, 15),
            email_normalizado=f"{user_id}@test.example.com",
            password_hash="hash",
            role=ArenaRole.ARENA_USER.value,
            _ai_api_key=None,
            prefered_language=prefered_language,
        )
    )


async def _insert_problem(
    conn: object,
    *,
    problem_id: str,
    owner_id: str,
    arena_number: int = 1,
    image_base64: str | None = None,
    image_mime: str | None = None,
    image_caption: str | None = None,
) -> None:
    """Insert a minimal arena_problems row, optionally with image data."""
    from sqlalchemy.ext.asyncio import AsyncConnection

    db_conn: AsyncConnection = conn  # type: ignore[assignment]
    await db_conn.execute(
        arena_problems.insert().values(
            id=problem_id,
            arena_number=arena_number,
            title="Query Test Problem",
            owner_id=owner_id,
            problem_statement="# Statement\nSolve it.",
            enabled=True,
            problem_image_base64=image_base64,
            problem_image_mime=image_mime,
            problem_image_caption=image_caption,
        )
    )


# ---------------------------------------------------------------------------
# ProblemData dataclass
# ---------------------------------------------------------------------------


def test_problem_data_all_none() -> None:
    """ProblemData can hold all-None values (problem not found path)."""
    pd = ProblemData(
        problem_statement=None,
        image_base64=None,
        image_mime=None,
        image_caption=None,
    )
    assert pd.problem_statement is None
    assert pd.image_base64 is None
    assert pd.image_mime is None
    assert pd.image_caption is None


def test_problem_data_with_values() -> None:
    """ProblemData stores provided values faithfully."""
    pd = ProblemData(
        problem_statement="# Problem\nText.",
        image_base64="aGVsbG8=",
        image_mime="image/png",
        image_caption="A figure.",
    )
    assert pd.problem_statement == "# Problem\nText."
    assert pd.image_base64 == "aGVsbG8="
    assert pd.image_mime == "image/png"
    assert pd.image_caption == "A figure."


@pytest.mark.asyncio
async def test_get_user_prefered_language_returns_stored_locale(engine: object) -> None:
    """The user preferred language query returns the stored locale."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await _insert_user_with_prefered_language(conn, "qry-user-locale", "pt-BR")
        result = await get_user_prefered_language(conn, "qry-user-locale")

    assert result == "pt-BR"


@pytest.mark.asyncio
async def test_get_user_prefered_language_defaults_for_missing_user(engine: object) -> None:
    """A missing user falls back to en-US."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        result = await get_user_prefered_language(conn, "missing-user")

    assert result == "en-US"


# ---------------------------------------------------------------------------
# get_problem_data — integration tests against the real DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_problem_data_returns_all_none_when_not_found(engine: object) -> None:
    """Non-existent problem_id returns ProblemData with all-None fields."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        result = await get_problem_data(conn, "non-existent-id")

    assert isinstance(result, ProblemData)
    assert result.problem_statement is None
    assert result.image_base64 is None
    assert result.image_mime is None
    assert result.image_caption is None


@pytest.mark.asyncio
async def test_get_problem_data_returns_statement_without_image(engine: object) -> None:
    """A problem without image returns the statement text and None image fields."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await _insert_user(conn, "qry-user-no-img")
        await _insert_problem(conn, problem_id="qry-prob-no-img", owner_id="qry-user-no-img", arena_number=10)
        result = await get_problem_data(conn, "qry-prob-no-img")

    assert result.problem_statement == "# Statement\nSolve it."
    assert result.image_base64 is None
    assert result.image_mime is None
    assert result.image_caption is None


@pytest.mark.asyncio
async def test_get_problem_data_returns_image_fields_when_present(engine: object) -> None:
    """A problem with image data returns all four fields correctly."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await _insert_user(conn, "qry-user-with-img")
        await _insert_problem(
            conn,
            problem_id="qry-prob-with-img",
            owner_id="qry-user-with-img",
            arena_number=11,
            image_base64="aGVsbG8=",
            image_mime="image/png",
            image_caption="Directed graph with 5 nodes.",
        )
        result = await get_problem_data(conn, "qry-prob-with-img")

    assert result.problem_statement == "# Statement\nSolve it."
    assert result.image_base64 == "aGVsbG8="
    assert result.image_mime == "image/png"
    assert result.image_caption == "Directed graph with 5 nodes."


@pytest.mark.asyncio
async def test_get_problem_data_returns_image_without_caption(engine: object) -> None:
    """A problem with image but no caption returns image fields and None caption."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await _insert_user(conn, "qry-user-no-caption")
        await _insert_problem(
            conn,
            problem_id="qry-prob-no-caption",
            owner_id="qry-user-no-caption",
            arena_number=12,
            image_base64="d29ybGQ=",
            image_mime="image/jpeg",
            image_caption=None,
        )
        result = await get_problem_data(conn, "qry-prob-no-caption")

    assert result.image_base64 == "d29ybGQ="
    assert result.image_mime == "image/jpeg"
    assert result.image_caption is None
