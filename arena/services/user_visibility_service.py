#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single owner of Arena's age-to-visibility rule (the "minor shield").

Every public read path resolves a user's displayed identity here and never from
``arena_users.nome`` directly, so the rule lives in exactly one place. The rule
itself is::

    status   = NEEDS_PARENTAL_CONSENT if dob is None else check_age(dob)
    shielded = status is not AgeStatus.ALLOWED

``shared.age_check`` stays a pure age oracle evaluated **per request**, so a
shielded account stops being shielded the moment it turns 18 with no scheduler
and no stored expiry. An unknown date of birth **fails closed**: it is treated
as a minor.

This module lives in ``arena/`` rather than ``shared/`` because the shield is
Arena product policy. It never touches ``ranking_visible`` -- a shielded user
stays in the ranking, under their pseudonym -- and the only consumer of any of
these flags outside Arena is ``shared.services.arena_rating``, which reads
``ranking_visible`` for affiliation aggregation and has no interest in age.

``shielded_users_clause()`` is the one unavoidable duplication of the rule, in
SQL. It is pinned by a test that executes both forms over the same boundary
table and asserts they agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta
from sqlalchemy import Date, literal, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import FromClause

from shared.age_check import AgeStatus, check_age
from shared.db_schema.arena import arena_users
from shared.services.email_validation import EmailValidationService

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from arena.models.arena_users import ArenaUser

__all__ = [
    "ADULT_AGE_YEARS",
    "DisplayIdentity",
    "display_identity_for_user",
    "is_shielded",
    "may_have_public_profile",
    "may_show_full_name",
    "resolve_display_identity",
    "shielded_users_clause",
]

ADULT_AGE_YEARS = 18


@dataclass(frozen=True)
class DisplayIdentity:
    """A user's identity as one public surface is allowed to render it.

    Attributes:
        user_id: Arena user UUID.
        display_name: The name to render -- the legal name only when the user
            is an adult who opted in, otherwise the pseudonymous username.
        is_pseudonymous: True when ``display_name`` is the username. Derived
            from the same boolean as ``display_name`` (it is its exact inverse),
            so the two fields cannot disagree.
        public_profile: The **effective** flag, after the shield. A stored
            ``public_profile=True`` on a shielded account resolves to False.
        masked_email: Partially hidden email for display, or None when the user
            is shielded. Masked email beside an affiliation and a country
            re-identifies a minor, so a shielded row carries no email at all.
    """

    user_id: str
    display_name: str
    is_pseudonymous: bool
    public_profile: bool
    masked_email: str | None


def is_shielded(date_of_birth: date | None, *, reference_date: date | None = None) -> bool:
    """Return whether an account is age-shielded on ``reference_date``.

    Args:
        date_of_birth: The account's stored date of birth, or None when unknown.
        reference_date: Date used as "today"; defaults to the current UTC date.

    Returns:
        bool: True for anyone under 18, and for an unknown date of birth, which
        fails closed. False only for a confirmed adult.
    """
    if date_of_birth is None:
        return True
    return check_age(date_of_birth, reference_date) is not AgeStatus.ALLOWED


def resolve_display_identity(
    *,
    user_id: str,
    full_name: str,
    username: str,
    date_of_birth: date | None,
    full_name_public: bool,
    public_profile: bool,
    ranking_visible: bool,
    email: str | None = None,
    reference_date: date | None = None,
) -> DisplayIdentity:
    """Resolve what a public surface may show for one user.

    Args:
        user_id: Arena user UUID.
        full_name: The account's stored legal name (``arena_users.nome``).
        username: The account's pseudonymous handle.
        date_of_birth: Stored date of birth, or None when unknown.
        full_name_public: The adult opt-in to publish the legal name.
        public_profile: The stored public-profile flag.
        ranking_visible: The stored ranking-visibility flag.
        email: Normalised email address, when the surface displays one.
        reference_date: Date used as "today"; defaults to the current UTC date.

    Returns:
        DisplayIdentity: The resolved, presentation-safe identity.
    """
    shielded = is_shielded(date_of_birth, reference_date=reference_date)
    # One boolean drives both the name and the pseudonymity flag, so a surface
    # can never render a legal name while reporting a pseudonym.
    show_full_name = full_name_public and not shielded
    return DisplayIdentity(
        user_id=user_id,
        display_name=full_name if show_full_name else username,
        is_pseudonymous=not show_full_name,
        public_profile=bool(public_profile and ranking_visible and not shielded),
        masked_email=None if (shielded or email is None) else EmailValidationService.mask(email),
    )


def display_identity_for_user(user: ArenaUser, *, reference_date: date | None = None) -> DisplayIdentity:
    """Resolve the display identity of a loaded ``ArenaUser`` row.

    Args:
        user: The Arena user whose identity to resolve.
        reference_date: Date used as "today"; defaults to the current UTC date.

    Returns:
        DisplayIdentity: The resolved, presentation-safe identity.
    """
    return resolve_display_identity(
        user_id=user.id,
        full_name=user.nome,
        username=user.username,
        date_of_birth=user.dta_nascimento,
        full_name_public=user.full_name_public,
        public_profile=user.public_profile,
        ranking_visible=user.ranking_visible,
        email=user.email_normalizado,
        reference_date=reference_date,
    )


def may_show_full_name(user: ArenaUser, *, reference_date: date | None = None) -> bool:
    """Return whether this account is allowed to publish its legal name.

    Args:
        user: The Arena user to test.
        reference_date: Date used as "today"; defaults to the current UTC date.

    Returns:
        bool: True only for a confirmed adult. It reports permission, not the
        opt-in itself, so a caller can offer the choice without granting it.
    """
    return not is_shielded(user.dta_nascimento, reference_date=reference_date)


def may_have_public_profile(user: ArenaUser, *, reference_date: date | None = None) -> bool:
    """Return whether this account is allowed to carry a public profile page.

    Args:
        user: The Arena user to test.
        reference_date: Date used as "today"; defaults to the current UTC date.

    Returns:
        bool: True only for a confirmed adult, regardless of the stored flag.
    """
    return not is_shielded(user.dta_nascimento, reference_date=reference_date)


def shielded_users_clause(
    users: FromClause = arena_users,
    *,
    reference_date: date | None = None,
) -> ColumnElement[bool]:
    """Return the SQL mirror of :func:`is_shielded`, for one table or alias.

    This is the rule's one unavoidable duplication. ``tests/arena`` executes
    both forms over the same boundary table and asserts they agree, so the two
    cannot drift.

    Args:
        users: The ``arena_users`` table, or an **alias** of it. Callers that
            build candidate-ID branches query an alias, and binding the
            predicate to the base table instead would add an unjoined FROM
            element -- a Cartesian product with an uncorrelated age test.
        reference_date: Date used as "today"; defaults to the current UTC date.

    Returns:
        ColumnElement[bool]: True for rows whose account is age-shielded.
    """
    today = reference_date or datetime.now(UTC).date()
    cutoff = today - relativedelta(years=ADULT_AGE_YEARS)
    # The cutoff is bound with an explicit Date type so each backend renders it
    # itself. A raw ISO string is rejected by asyncpg, which is strictly typed,
    # and sqlite3's implicit date adapter is deprecated; a PostgreSQL INTERVAL
    # literal would not run on the SQLite test path at all.
    return or_(
        users.c.dta_nascimento.is_(None),
        users.c.dta_nascimento > literal(cutoff, Date()),
    )
