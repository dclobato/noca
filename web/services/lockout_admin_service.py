#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Web-side vocabulary for the administrative lockout reset.

The shared primitive (:mod:`shared.services.auth_lockout_admin`) works on
account *hashes*; this module knows which raw identifiers Web's own throttle
buckets hash for one login: the typed name itself (the uberadmin login form),
the contest-scoped name the contest login form hashes, and the type-prefixed
ids the password re-confirmation keys on.

A contest login is unique only *per contest* (``uq_users_contest_username``),
so the ``contest-login`` bucket is keyed on ``{contest_id}:{username}`` rather
than on the bare name -- see :func:`contest_login_identifier`. That is what
makes an unlock expressible per contest: releasing one contest's ``admin``
while another contest's ``admin`` stays locked. The scope is therefore the
operator's explicit choice, never a default, and this module expresses both
halves of it: ``contest_id=None`` means *all contests* and covers the global
UberAdmin buckets too, while a contest id covers exactly that contest.

An UberAdmin clears the ``web`` buckets and the ``animator`` ones: the animator
has no administrative surface of its own, and its control lockout belongs to
the same contest operation Web administers.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.auth_lockout_admin import LockoutSubject, account_identifier_hashes
from shared.services.auth_rate_limit import hash_identifier
from web.config import settings
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.contest_service import get_active_contests_grouped

__all__ = [
    "ALL_CONTESTS_SCOPE",
    "WEB_LOCKOUT_MODULES",
    "ContestChoice",
    "ResolvedLogin",
    "active_contest_choices",
    "contest_login_identifier",
    "resolve_login",
    "subject_for_hashes",
    "subject_for_ip",
]

ALL_CONTESTS_SCOPE = "all"
"""The form value naming the deliberate, deployment-wide unlock."""

WEB_LOCKOUT_MODULES: tuple[str, ...] = ("web", "animator")
"""The key modules an UberAdmin may clear."""


@dataclass(frozen=True, slots=True)
class ContestChoice:
    """One selectable unlock scope, as offered to the operator."""

    contest_id: str
    label: str


@dataclass(frozen=True, slots=True)
class ResolvedLogin:
    """What a typed login turned out to name, within the chosen scope.

    Attributes:
        hashes: Every throttle bucket hash the unlock should clear.
        uberadmin_id: The matching UberAdmin, when the scope covers one.
        user_ids: The matching contest users, within the chosen scope.
        contest_labels: ``hash -> contest label`` for each ``contest-login``
            bucket this resolution built, so the status panel can name the
            contest a lock belongs to. Hashes are one-way, so a lock whose
            hash is in no map stays unlabelled rather than guessed.
        scope_label: How the chosen scope reads in the audit row.
    """

    hashes: frozenset[str]
    uberadmin_id: str | None
    user_ids: tuple[str, ...]
    contest_labels: dict[str, str]
    scope_label: str

    @property
    def matched(self) -> bool:
        """Whether the login names at least one account."""
        return self.uberadmin_id is not None or bool(self.user_ids)

    @property
    def summary(self) -> str:
        """Short wording of what matched, for the audit row."""
        parts: list[str] = []
        if self.uberadmin_id is not None:
            parts.append("uberadmin")
        if self.user_ids:
            parts.append(f"{len(self.user_ids)} contest user{'s' if len(self.user_ids) != 1 else ''}")
        return ", ".join(parts) or "no account"


def contest_login_identifier(contest_id: str, raw: str) -> str | None:
    """The raw identifier the ``web``/``contest-login`` bucket hashes.

    The contest is part of the key because a contest login is unique only per
    contest: on the bare name, five failures against the least important
    contest on the deployment would lock that name out of every contest, and
    an administrative unlock could never be finer than the name either.

    The typed name is stripped **before** the contest is prefixed, and a blank
    name yields ``None``. Both matter: :func:`~shared.services.auth_rate_limit.
    normalize_identifier` strips and casefolds the *whole* string, so prefixing
    an unstripped ``"  Team042 "`` would keep the inner spaces and hash to
    something no resolver could rebuild, and prefixing a blank name would mint
    an account bucket where today there is none.

    Args:
        contest_id: The contest being authenticated against (the row id, not
            the slug: a slug can be renamed while a lock is live).
        raw: The username exactly as typed.

    Returns:
        ``{contest_id}:{username}``, or ``None`` when nothing was typed.
    """
    name = raw.strip()
    return f"{contest_id}:{name}" if name else None


async def active_contest_choices(session: AsyncSession) -> list[ContestChoice]:
    """The contests an operator may pick as an unlock scope.

    Reuses the UberAdmin dashboard's own query, so the select cannot list a
    contest the dashboard does not. It flattens all three
    :class:`~web.services.contest_service.ContestDashboardGroups` collections
    and keeps the dashboard's definition of active, which includes contests
    that have ended but were never deactivated.

    Only an *active* contest can mint a ``contest-login`` lock, because
    ``get_contest_by_slug`` refuses an inactive one; a lock left behind by a
    contest deactivated in the last few minutes is therefore unreachable here
    and expires on its own.

    Args:
        session: Active async database session.

    Returns:
        One choice per active contest, ordered by name. The label carries the
        login slug too, since contest names are not unique and slugs are.
    """
    groups = await get_active_contests_grouped(session)
    contests = [*groups.live_contests, *groups.upcoming_contests, *groups.past_contests]
    return [
        ContestChoice(contest_id=contest.id, label=f"{contest.contest_name} ({contest.login_slug})")
        for contest in sorted(contests, key=lambda contest: (contest.contest_name.casefold(), contest.login_slug))
    ]


async def resolve_login(session: AsyncSession, raw: str, *, contest_id: str | None = None) -> ResolvedLogin:
    """Turn a typed login, within one scope, into the hashes to clear.

    With ``contest_id`` given, only that contest is touched: the scoped
    ``contest-login`` identifier and the ``password-confirm`` buckets of that
    contest's users carrying the name. The bare-name hash (the UberAdmin
    ``login`` bucket) and the ``uberadmin:<id>`` bucket are deliberately left
    alone -- an UberAdmin is not a contest, and reaching a global bucket from a
    contest-scoped unlock would make "leave the other contests locked" false.

    With ``contest_id`` of ``None`` (*all contests*), everything is covered:
    the bare name, the UberAdmin's own bucket, and one scoped identifier per
    contest carrying the name, plus every matching user's bucket.

    Args:
        session: Active async database session.
        raw: The login exactly as typed.
        contest_id: The contest to confine the unlock to, or ``None`` for all.

    Returns:
        The hashes, what they matched, and the ``hash -> contest`` labels.
    """
    text = raw.strip()
    rows = (
        await session.execute(
            select(User.id, Contest.id, Contest.contest_name, Contest.login_slug)
            .join(Contest, Contest.id == User.contest_id)
            .where(User.username == text)
        )
    ).all()
    if contest_id is not None:
        rows = [row for row in rows if row[1] == contest_id]

    identifiers: list[str | None] = []
    contest_labels: dict[str, str] = {}
    uberadmin_id: str | None = None
    if contest_id is None:
        uberadmin_id = await session.scalar(select(UberAdmin.id).where(UberAdmin.username == text))
        identifiers.append(text)
        if uberadmin_id is not None:
            identifiers.append(f"uberadmin:{uberadmin_id}")
    scoped: dict[str, str] = {row[1]: f"{row[2]} ({row[3]})" for row in rows}
    if contest_id is not None:
        scoped.setdefault(contest_id, await _contest_label(session, contest_id))
    for scope_id, label in scoped.items():
        identifier = contest_login_identifier(scope_id, text)
        digest = hash_identifier(identifier, secret=settings.JWT_SECRET_KEY)
        if digest is not None:
            identifiers.append(identifier)
            contest_labels[digest] = label
    user_ids = tuple(str(row[0]) for row in rows)
    identifiers.extend(f"user:{user_id}" for user_id in user_ids)
    return ResolvedLogin(
        hashes=account_identifier_hashes(identifiers, secret=settings.JWT_SECRET_KEY),
        uberadmin_id=uberadmin_id,
        user_ids=user_ids,
        contest_labels=contest_labels,
        scope_label="all contests" if contest_id is None else scoped.get(contest_id, contest_id),
    )


async def _contest_label(session: AsyncSession, contest_id: str) -> str:
    """Name one contest for a scope with no matching user (an unknown login)."""
    row = (
        await session.execute(select(Contest.contest_name, Contest.login_slug).where(Contest.id == contest_id))
    ).first()
    return f"{row[0]} ({row[1]})" if row is not None else contest_id


def subject_for_ip(ip: str) -> LockoutSubject:
    """The Web-scoped subject for one validated address."""
    return LockoutSubject(modules=WEB_LOCKOUT_MODULES, ip=ip)


def subject_for_hashes(hashes: frozenset[str]) -> LockoutSubject:
    """The Web-scoped subject for a set of account hashes."""
    return LockoutSubject(modules=WEB_LOCKOUT_MODULES, identifier_hashes=hashes)
