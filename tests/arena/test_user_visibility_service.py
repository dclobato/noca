#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena minor shield's age-to-visibility rule.

The Python predicate and its SQL mirror are asserted to agree over one shared
boundary table, so the module's single unavoidable duplication cannot drift.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from werkzeug.security import generate_password_hash

from arena.models.arena_users import ArenaUser
from arena.services.identity_search_service import _public_postgres_user_candidates
from arena.services.user_visibility_service import (
    DisplayIdentity,
    is_shielded,
    resolve_display_identity,
    shielded_users_clause,
)
from shared.db_schema.arena import arena_users
from shared.enumerations import ArenaRole

_REFERENCE = date(2026, 8, 31)

# (label, date of birth, shielded on _REFERENCE)
_BOUNDARY_CASES: list[tuple[str, date | None, bool]] = [
    ("unknown date of birth fails closed", None, True),
    ("twelve, under the hard block", date(2014, 1, 1), True),
    ("thirteen, consent age", date(2013, 1, 1), True),
    ("seventeen years and 364 days", date(2008, 9, 1), True),
    ("exactly eighteen today", date(2008, 8, 31), False),
    ("eighteen years and a day", date(2008, 8, 30), False),
    ("nineteen", date(2007, 5, 4), False),
    # A 29 February birth date has no anniversary on a non-leap reference year.
    # 2026 is not a leap year, so this user turns 18 on 2026-03-01 and is an
    # adult by 2026-08-31; the leap-day arithmetic must not shift that.
    ("leap-day birth, already adult", date(2008, 2, 29), False),
    ("leap-day birth, still a minor", date(2012, 2, 29), True),
]

_FULL_NAME = "Ada Lovelace"
_USERNAME = "coruja-serena-042"
_EMAIL = "ada@example.org"


def _identity(
    *,
    date_of_birth: date | None,
    full_name_public: bool = False,
    public_profile: bool = False,
    ranking_visible: bool = True,
    email: str | None = None,
) -> DisplayIdentity:
    """Resolve a display identity with the shared fixture values."""
    return resolve_display_identity(
        user_id="user-1",
        full_name=_FULL_NAME,
        username=_USERNAME,
        date_of_birth=date_of_birth,
        full_name_public=full_name_public,
        public_profile=public_profile,
        ranking_visible=ranking_visible,
        email=email,
        reference_date=_REFERENCE,
    )


@pytest.mark.parametrize(("label", "dob", "expected"), _BOUNDARY_CASES)
def test_is_shielded_across_the_age_boundary(label: str, dob: date | None, expected: bool) -> None:
    """The shield turns off exactly on the eighteenth birthday."""
    assert is_shielded(dob, reference_date=_REFERENCE) is expected, label


def test_shielded_user_shows_the_username_even_when_opted_in() -> None:
    """A minor cannot publish their legal name, opt-in or not."""
    identity = _identity(date_of_birth=date(2009, 5, 1), full_name_public=True)
    assert identity.display_name == _USERNAME
    assert _FULL_NAME not in identity.display_name


def test_adult_opt_in_shows_the_full_name() -> None:
    """An adult who opted in publishes their legal name."""
    assert _identity(date_of_birth=date(1990, 1, 1), full_name_public=True).display_name == _FULL_NAME


def test_adult_default_shows_the_username() -> None:
    """Pseudonymous by default: an adult must opt in explicitly."""
    assert _identity(date_of_birth=date(1990, 1, 1), full_name_public=False).display_name == _USERNAME


def test_unknown_date_of_birth_shows_the_username() -> None:
    """A row that never stated an age is treated as a minor."""
    assert _identity(date_of_birth=None, full_name_public=True).display_name == _USERNAME


@pytest.mark.parametrize(
    ("dob", "full_name_public", "expected"),
    [
        (date(2009, 5, 1), True, True),
        (date(2009, 5, 1), False, True),
        (date(1990, 1, 1), False, True),
        (date(1990, 1, 1), True, False),
        (None, True, True),
    ],
)
def test_is_pseudonymous_truth_table(dob: date | None, full_name_public: bool, expected: bool) -> None:
    """``is_pseudonymous`` is the exact inverse of "the legal name is shown".

    Asserted against the derivation rather than against
    ``display_name == username``: those coincide only because the fixtures give
    the two fields different values, which is an accident of the fixture and not
    the invariant.
    """
    identity = _identity(date_of_birth=dob, full_name_public=full_name_public)
    shielded = is_shielded(dob, reference_date=_REFERENCE)
    assert identity.is_pseudonymous is expected
    assert identity.is_pseudonymous is not (full_name_public and not shielded)


def test_shielded_user_has_no_effective_public_profile() -> None:
    """A stale ``public_profile=True`` on a minor resolves to False."""
    identity = _identity(date_of_birth=date(2009, 5, 1), public_profile=True, ranking_visible=True)
    assert identity.public_profile is False


def test_adult_public_profile_requires_ranking_visibility() -> None:
    """The public-profile flag still depends on ranking visibility."""
    assert _identity(date_of_birth=date(1990, 1, 1), public_profile=True, ranking_visible=False).public_profile is False
    assert _identity(date_of_birth=date(1990, 1, 1), public_profile=True, ranking_visible=True).public_profile is True


def test_shielded_user_carries_no_masked_email() -> None:
    """Masked email plus affiliation plus country re-identifies a minor."""
    assert _identity(date_of_birth=date(2009, 5, 1), email=_EMAIL).masked_email is None


def test_adult_carries_a_masked_email() -> None:
    """An adult's address is masked, not withheld."""
    masked = _identity(date_of_birth=date(1990, 1, 1), email=_EMAIL).masked_email
    assert masked is not None
    assert masked != _EMAIL


def test_missing_email_yields_none_rather_than_a_crash() -> None:
    """Surfaces that display no email pass none and get none back."""
    assert _identity(date_of_birth=date(1990, 1, 1), email=None).masked_email is None


def test_shielded_users_clause_compiles_on_both_dialects() -> None:
    """The clause renders on PostgreSQL and on the SQLite test path alike."""
    clause = shielded_users_clause(reference_date=_REFERENCE)
    for dialect in (postgresql.dialect(), sqlite.dialect()):  # type: ignore[no-untyped-call]
        rendered = str(clause.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
        assert "dta_nascimento" in rendered
        # Never an INTERVAL literal: the SQLite test path cannot execute one.
        assert "INTERVAL" not in rendered.upper()


def test_shielded_users_clause_binds_to_the_search_alias() -> None:
    """The public search branches must correlate the shield to their own alias.

    Binding it to ``arena_users`` while the branch queries an alias would add a
    second, unjoined FROM element -- a cross join with an uncorrelated age test.
    """
    branch = _public_postgres_user_candidates("lovelace")
    rendered = str(
        branch.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "search_arena_user" in rendered
    # arena_users may appear only as the alias's origin, never as a FROM of its
    # own: one occurrence per branch, always immediately followed by the alias.
    assert rendered.count("arena_users") == rendered.count("arena_users AS search_arena_user")


async def _make_user(session: AsyncSession, *, email: str, dta_nascimento: date | None) -> ArenaUser:
    """Persist a minimal Arena user carrying one date of birth."""
    user = ArenaUser(
        nome="Boundary Case",
        email_normalizado=email,
        password_hash=generate_password_hash("TestPass1!", method="pbkdf2:sha256:1000"),
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=dta_nascimento,
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_sql_clause_agrees_with_the_python_predicate(session: AsyncSession) -> None:
    """The SQL mirror and :func:`is_shielded` classify every boundary row alike."""
    expected: dict[str, bool] = {}
    for index, (label, dob, shielded) in enumerate(_BOUNDARY_CASES):
        user = await _make_user(session, email=f"boundary{index}@example.org", dta_nascimento=dob)
        expected[user.id] = shielded
        assert is_shielded(dob, reference_date=_REFERENCE) is shielded, label

    rows = (
        await session.execute(
            select(arena_users.c.id).where(
                arena_users.c.id.in_(expected),
                shielded_users_clause(reference_date=_REFERENCE),
            )
        )
    ).scalars()
    matched = set(rows)

    assert matched == {user_id for user_id, shielded in expected.items() if shielded}
