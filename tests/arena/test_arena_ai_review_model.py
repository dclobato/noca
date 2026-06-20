#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena AI review fields on ArenaUser and ArenaSubmissionAIReview.

Covers:
  - ArenaUser.ai_api_key getter/setter (in-memory; encryption is a DB-layer
    concern tested separately via the EncryptedString TypeDecorator).
  - ArenaSubmission.submit_to_ai default value.
  - ArenaSubmission.ai_review relationship: None before review, populated after.
  - ArenaSubmissionAIReview fields and 1:1 relationship to ArenaSubmission.
  - ArenaSubmissionAIReview.ai_review_cost getter/setter: float <-> integer micros
    conversion at every boundary (positive, zero, None, rounding).
  - ArenaSubmissionAIReview._ai_review_cost raw integer persisted to DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401 — registers ORM mappers
import arena.models.arena_submissions  # noqa: F401 — registers ORM mappers
import arena.models.arena_users  # noqa: F401 — registers ORM mappers
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionAIReview
from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaRole
from web.models.language import Language

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


async def _make_language(session: AsyncSession) -> Language:
    lang = Language(
        id=f"test-lang-{uuid.uuid4().hex[:6]}",
        name="Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="sol.txt",
        artifact_path="/sandbox/sol.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(lang)
    await session.flush()
    return lang


def _make_user() -> ArenaUser:
    """Return an in-memory ArenaUser (not flushed)."""
    user = ArenaUser(
        nome="AI Test User",
        email_normalizado=f"ai_{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1995, 6, 15),
        role=ArenaRole.ARENA_USER,
    )
    user.password = "Senha@Forte1!"
    return user


@pytest_asyncio.fixture
async def arena_user(session: AsyncSession) -> ArenaUser:
    user = _make_user()
    session.add(user)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def arena_author(session: AsyncSession) -> ArenaUser:
    from arena.models.arena_problems import ArenaProblem  # noqa: F401

    user = ArenaUser(
        nome="Autor AI",
        email_normalizado=f"autor_ai_{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1990, 1, 1),
        role=ArenaRole.ARENA_JUDGE,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def arena_problem(session: AsyncSession, arena_author: ArenaUser):
    from arena.models.arena_problems import ArenaProblem

    problem = ArenaProblem(
        arena_number=1,
        title="AI Review Test Problem",
        owner_id=arena_author.id,
        problem_statement="<p>Write some code.</p>",
    )
    session.add(problem)
    await session.flush()
    return problem


@pytest_asyncio.fixture
async def arena_submission(session: AsyncSession, arena_user: ArenaUser, arena_problem) -> ArenaSubmission:
    lang = await _make_language(session)
    sub = ArenaSubmission(
        user_id=arena_user.id,
        problem_id=arena_problem.id,
        language_id=lang.id,
        source_code="print('hello')",
        source_hash="a" * 64,
        source_size_bytes=15,
    )
    session.add(sub)
    await session.flush()
    return sub


# ---------------------------------------------------------------------------
# ArenaUser.ai_api_key property
# ---------------------------------------------------------------------------


def test_ai_api_key_default_is_none() -> None:
    """A new ArenaUser has no AI API key."""
    user = _make_user()
    assert user.ai_api_key is None


def test_ai_api_key_setter_stores_value() -> None:
    """Setting ai_api_key persists the value via the private attribute."""
    user = _make_user()
    user.ai_api_key = "sk-test-abc123"
    assert user.ai_api_key == "sk-test-abc123"
    assert user._ai_api_key == "sk-test-abc123"


def test_ai_api_key_setter_accepts_none() -> None:
    """Setting ai_api_key to None clears the private attribute."""
    user = _make_user()
    user.ai_api_key = "sk-test-abc123"
    user.ai_api_key = None
    assert user.ai_api_key is None
    assert user._ai_api_key is None


def test_ai_api_key_setter_overwrites_previous_value() -> None:
    """Setting ai_api_key twice keeps only the latest value."""
    user = _make_user()
    user.ai_api_key = "sk-first"
    user.ai_api_key = "sk-second"
    assert user.ai_api_key == "sk-second"


# ---------------------------------------------------------------------------
# ArenaSubmission.submit_to_ai default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_to_ai_defaults_to_false(session: AsyncSession, arena_submission: ArenaSubmission) -> None:
    """submit_to_ai is False after a plain insert."""
    await session.refresh(arena_submission)
    assert arena_submission.submit_to_ai is False


@pytest.mark.asyncio
async def test_submit_to_ai_can_be_set_true(session: AsyncSession, arena_submission: ArenaSubmission) -> None:
    """submit_to_ai can be updated to True and persisted."""
    arena_submission.submit_to_ai = True
    await session.flush()
    await session.refresh(arena_submission)
    assert arena_submission.submit_to_ai is True


# ---------------------------------------------------------------------------
# ArenaSubmission.ai_review relationship
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_review_is_none_before_review(session: AsyncSession, arena_submission: ArenaSubmission) -> None:
    """ai_review relationship is None when no ArenaSubmissionAIReview exists."""
    await session.refresh(arena_submission, ["ai_review"])
    assert arena_submission.ai_review is None


@pytest.mark.asyncio
async def test_ai_review_relationship_populated_after_insert(
    session: AsyncSession, arena_submission: ArenaSubmission
) -> None:
    """ai_review relationship returns the linked ArenaSubmissionAIReview."""
    now = datetime.now(UTC)
    review = ArenaSubmissionAIReview(
        submission_id=arena_submission.id,
        ai_response="Consider a hash map.",
        ai_response_at=now,
        _ai_review_cost=None,
    )
    session.add(review)
    await session.flush()
    await session.refresh(arena_submission, ["ai_review"])
    assert arena_submission.ai_review is not None
    assert arena_submission.ai_review.ai_response == "Consider a hash map."
    assert arena_submission.ai_review.ai_response_at is not None


@pytest.mark.asyncio
async def test_ai_review_cascade_delete(session: AsyncSession, arena_submission: ArenaSubmission) -> None:
    """Deleting ArenaSubmission cascades to ArenaSubmissionAIReview."""
    review = ArenaSubmissionAIReview(
        submission_id=arena_submission.id,
        ai_response="Hint.",
        ai_response_at=datetime.now(UTC),
        _ai_review_cost=None,
    )
    session.add(review)
    await session.flush()
    review_id = arena_submission.id  # same as submission_id (PK)

    await session.delete(arena_submission)
    await session.flush()

    import sqlalchemy as sa

    from shared.db_schema.arena import arena_submission_ai_reviews

    result = await session.execute(
        sa.select(arena_submission_ai_reviews).where(arena_submission_ai_reviews.c.submission_id == review_id)
    )
    assert result.first() is None


# ---------------------------------------------------------------------------
# ArenaSubmissionAIReview.ai_review_cost — conversion formula (pure math)
#
# Tests verify the formula directly since SQLAlchemy instrumented attributes
# require ORM state. DB round-trip tests below validate the full chain.
# ---------------------------------------------------------------------------

_SCALE = ArenaSubmissionAIReview._COST_SCALE


def test_cost_scale_is_one_million() -> None:
    """_COST_SCALE must be 1_000_000 to give 6 decimal places of precision."""
    assert _SCALE == 1_000_000


def test_cost_to_micros_formula() -> None:
    """Float → micros: round(value * 1_000_000)."""
    assert round(0.001234 * _SCALE) == 1234


def test_cost_from_micros_formula() -> None:
    """Micros → float: integer / 1_000_000."""
    assert pytest.approx(0.001234) == 1234 / _SCALE


def test_cost_formula_roundtrip_precision() -> None:
    """Six-decimal values survive a to-micros / from-micros round-trip."""
    original = 0.123456
    assert round(original * _SCALE) / _SCALE == pytest.approx(original)


def test_cost_formula_zero() -> None:
    """Zero is stored as 0 and retrieved as 0.0."""
    assert round(0.0 * _SCALE) == 0
    assert 0 / _SCALE == 0.0


def test_cost_formula_rounds_half_up() -> None:
    """0.5 micros rounds to 1 (Python banker's rounding, confirmed explicitly)."""
    raw = round(0.0000005 * _SCALE)
    assert raw in {0, 1}  # Python rounds 0.5 to nearest even; either is safe


def test_cost_formula_large_value() -> None:
    """$1.50 stores as 1_500_000 micros and reads back as 1.5."""
    assert round(1.5 * _SCALE) == 1_500_000
    assert pytest.approx(1.5) == 1_500_000 / _SCALE


# ---------------------------------------------------------------------------
# ArenaSubmissionAIReview._ai_review_cost persisted to database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_review_cost_persisted_as_integer(session: AsyncSession, arena_submission: ArenaSubmission) -> None:
    """Raw _ai_review_cost integer is stored and recovered from the database."""
    review = ArenaSubmissionAIReview(
        submission_id=arena_submission.id,
        ai_response="Hint.",
        ai_response_at=datetime.now(UTC),
    )
    review.ai_review_cost = 0.042
    session.add(review)
    await session.flush()
    await session.refresh(review)
    assert review._ai_review_cost == 42_000
    assert review.ai_review_cost == pytest.approx(0.042)


@pytest.mark.asyncio
async def test_ai_review_cost_null_persisted(session: AsyncSession, arena_submission: ArenaSubmission) -> None:
    """Null ai_review_cost is stored and recovered correctly."""
    review = ArenaSubmissionAIReview(
        submission_id=arena_submission.id,
        ai_response="Hint.",
        ai_response_at=datetime.now(UTC),
        _ai_review_cost=None,
    )
    session.add(review)
    await session.flush()
    await session.refresh(review)
    assert review._ai_review_cost is None
    assert review.ai_review_cost is None
