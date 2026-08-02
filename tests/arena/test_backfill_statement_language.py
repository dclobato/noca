#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena statement-language backfill script."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Update, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from shared.db_schema.arena import arena_problems
from shared.enumerations import ArenaRole, StatementLanguage

_PT = """# Soma de dois números

Dado dois números inteiros, escreva um programa que calcule a soma deles e
imprima o resultado na saída padrão do seu programa.
"""

_EN = """# Sum of two numbers

Given two integers, write a program that computes their sum and prints the
result to the standard output of your program.
"""


def _load_script() -> ModuleType:
    """Import the backfill script by path (``scripts/`` is not an importable package)."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "arena" / "backfill_statement_language.py"
    name = "noca_backfill_statement_language"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


async def _make_owner(session: AsyncSession) -> ArenaUser:
    user = ArenaUser(
        nome="Backfill Owner",
        email_normalizado=f"backfill-{uuid.uuid4().hex[:8]}@test.example.com",
        password_hash="hash",
        role=ArenaRole.ARENA_JUDGE,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_problem(
    session: AsyncSession,
    owner: ArenaUser,
    *,
    statement: str,
    language: StatementLanguage | None = None,
) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
        title="Backfill Problem",
        owner_id=owner.id,
        author_is_owner=True,
        enabled=False,
        problem_statement=statement,
        statement_language=language,
    )
    session.add(problem)
    await session.flush()
    return problem


async def _language_of(session: AsyncSession, problem_id: str) -> StatementLanguage | None:
    return (
        await session.execute(select(arena_problems.c.statement_language).where(arena_problems.c.id == problem_id))
    ).scalar_one()


@pytest.mark.asyncio
async def test_backfill_fills_only_rows_without_a_language(session: AsyncSession) -> None:
    owner = await _make_owner(session)
    empty = await _make_problem(session, owner, statement=_PT)
    already_set = await _make_problem(session, owner, statement=_PT, language=StatementLanguage.EN)
    await session.commit()

    summary = await script.backfill_statement_languages(session)

    assert await _language_of(session, empty.id) == StatementLanguage.PT
    # A row that already stated its language keeps it, even a "wrong" one.
    assert await _language_of(session, already_set.id) == StatementLanguage.EN
    assert summary.scanned == 1
    assert summary.detected == 1
    assert summary.updated == 1
    assert summary.skipped == 0


@pytest.mark.asyncio
async def test_backfill_dry_run_writes_nothing(session: AsyncSession) -> None:
    owner = await _make_owner(session)
    problem = await _make_problem(session, owner, statement=_PT)
    await session.commit()

    summary = await script.backfill_statement_languages(session, dry_run=True)

    assert await _language_of(session, problem.id) is None
    assert summary.detected == 1
    assert summary.updated == 0


@pytest.mark.asyncio
async def test_backfill_leaves_undetectable_statements_null(session: AsyncSession) -> None:
    owner = await _make_owner(session)
    problem = await _make_problem(session, owner, statement="Oi.")
    await session.commit()

    summary = await script.backfill_statement_languages(session)

    assert await _language_of(session, problem.id) is None
    assert summary.undetectable == 1
    assert summary.updated == 0


@pytest.mark.asyncio
async def test_backfill_honours_limit_and_batch_size(session: AsyncSession) -> None:
    owner = await _make_owner(session)
    for _ in range(5):
        await _make_problem(session, owner, statement=_EN)
    await session.commit()

    summary = await script.backfill_statement_languages(session, batch_size=2, limit=3)

    assert summary.scanned == 3
    assert summary.updated == 3
    remaining = (
        await session.execute(select(arena_problems.c.id).where(arena_problems.c.statement_language.is_(None)))
    ).all()
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_backfill_does_not_overwrite_a_concurrent_write(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A language stored between the read and the guarded UPDATE must survive.

    The row the other writer claimed must also not cost the *next* unresolved row
    its turn: a row that leaves the ``IS NULL`` set still has to be paged over
    correctly.
    """
    owner = await _make_owner(session)
    raced_problem = await _make_problem(session, owner, statement=_PT)
    other_problem = await _make_problem(session, owner, statement=_EN)
    await session.commit()
    # The keyset cursor walks ids in order; make the raced row come first.
    first, second = sorted([raced_problem, other_problem], key=lambda item: item.id)

    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    original_execute = session.execute
    raced = False

    async def _execute_with_race(statement: object, *args: object, **kwargs: object) -> object:
        """Let an independent, committed session claim the first row mid-batch."""
        nonlocal raced
        if not raced and isinstance(statement, Update):
            raced = True
            async with factory() as other_session:
                await other_session.execute(
                    update(arena_problems)
                    .where(arena_problems.c.id == first.id)
                    .values(statement_language=StatementLanguage.ES)
                )
                await other_session.commit()
        return await original_execute(statement, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session, "execute", _execute_with_race)

    summary = await script.backfill_statement_languages(session, batch_size=1)
    monkeypatch.undo()

    # The concurrent value survives, and the second row is still processed.
    assert await _language_of(session, first.id) == StatementLanguage.ES
    assert await _language_of(session, second.id) is not None
    assert summary.scanned == 2
    assert summary.updated == 1
    assert summary.skipped == 1


def test_backfill_script_is_bundled_in_the_arena_image() -> None:
    """The Arena Dockerfile copies scripts one by one, so an omission is silent."""
    dockerfile = Path(__file__).resolve().parents[2] / "containers" / "arena" / "Dockerfile"
    assert "scripts/arena/backfill_statement_language.py" in dockerfile.read_text(encoding="utf-8")
