#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM tests for the Arena user badge ledger."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_badges import ArenaUserBadge
from arena.models.arena_users import ArenaUser
from shared.db_schema.arena import arena_problems, arena_submissions
from shared.enumerations import ArenaBadge, ArenaRole, ProblemValidatorType
from web.models.language import Language


async def _create_user(session: AsyncSession) -> ArenaUser:
    """Persist a minimal Arena user for badge tests.

    Args:
        session: Async database session.

    Returns:
        ArenaUser: Flushed user ready to own badges.
    """
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Badge User",
        email_normalizado=f"{uuid.uuid4()}@test.example",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        aceitou_termos_privacidade=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=1,
        prefered_language="en-US",
    )
    user.password = "StrongPass1!"
    session.add(user)
    await session.flush()
    return user


async def _create_submission(session: AsyncSession, user: ArenaUser) -> str:
    """Persist a submission a badge can name; return its id.

    ``arena_user_badges.submission_id`` is ``NOT NULL``, so every badge in these
    tests needs real work to point at.

    Args:
        session: Async database session.
        user: The submitting user.

    Returns:
        str: The new submission's id.
    """
    language = Language(
        id=f"lang-{uuid.uuid4().hex[:8]}",
        name="Badge Test Lang",
        icon="devicon-c-plain",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="source.c",
        artifact_path="/sandbox/a.out",
        artifact_is_source=False,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    problem_id = str(uuid.uuid4())
    await session.execute(
        arena_problems.insert().values(
            id=problem_id,
            arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
            title=f"Badge Problem {uuid.uuid4().hex[:8]}",
            owner_id=user.id,
            problem_statement="Test problem.",
            validator_type=ProblemValidatorType.STANDARD,
        )
    )
    submission_id = str(uuid.uuid4())
    await session.execute(
        arena_submissions.insert().values(
            id=submission_id,
            user_id=user.id,
            problem_id=problem_id,
            language_id=language.id,
            source_code="int main(){}",
            source_hash=uuid.uuid4().hex,
            source_size_bytes=12,
        )
    )
    return submission_id


@pytest.mark.asyncio
async def test_badge_relationship_round_trips(session: AsyncSession) -> None:
    """Badges added to a user load back through the relationship."""
    user = await _create_user(session)
    submission_id = await _create_submission(session, user)
    awarded = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.HELLO_WORLD,
            awarded_at=awarded,
            submission_id=submission_id,
        )
    )
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.ONE_SHOT,
            awarded_at=awarded,
            submission_id=submission_id,
        )
    )
    await session.flush()

    await session.refresh(user, ["badges"])
    assert {b.badge for b in user.badges} == {ArenaBadge.HELLO_WORLD, ArenaBadge.ONE_SHOT}

    loaded = await session.scalar(
        select(ArenaUser).where(ArenaUser.id == user.id).options(selectinload(ArenaUser.badges))
    )
    assert loaded is not None
    assert all(b.user_id == user.id for b in loaded.badges)


@pytest.mark.asyncio
async def test_badge_is_unique_per_user(session: AsyncSession) -> None:
    """The same badge cannot be awarded to one user twice."""
    user = await _create_user(session)
    submission_id = await _create_submission(session, user)
    awarded = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.FULL_CLEAR,
            awarded_at=awarded,
            submission_id=submission_id,
        )
    )
    await session.flush()
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.FULL_CLEAR,
            awarded_at=awarded,
            submission_id=submission_id,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
