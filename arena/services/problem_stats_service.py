#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read access to precomputed per-problem statistics.

The statistics themselves are computed periodically by the rating worker
(``shared.services.arena_problem_stats``) and stored as a JSON snapshot in
``arena_problem_statistics``. This service only reads the latest snapshot for
the Arena statistics page; it performs no aggregation. The viewer-specific
decoration it adds -- timezone-formatted timestamps and profile links -- is
resolved at request time because the snapshot goes stale: a solver's profile
visibility can change between two rating cycles.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services.profile_visibility import can_view_public_profile
from arena.services.user_timezone_service import format_user_datetime
from arena.services.user_visibility_service import resolve_display_identity
from shared.db_schema.arena import arena_problem_statistics as _arena_problem_statistics
from shared.db_schema.arena import arena_users as _arena_users

_SOLVER_KEYS = ("first_solver", "last_solver")

# Shown in place of a solver whose account is gone. The snapshot still carries
# the legal name it froze, and a deleted row cannot be aged, so there is no way
# to know whether publishing it is allowed -- withhold it.
UNKNOWN_SOLVER_NAME = "—"


@dataclass(frozen=True)
class _SolverIdentity:
    """One solver's age-shielded display name and profile-link permission."""

    display_name: str
    viewable: bool


async def get_problem_statistics(session: AsyncSession, problem_id: str) -> dict[str, Any] | None:
    """Return the latest statistics snapshot for a problem, or ``None``.

    Args:
        session: Active async database session.
        problem_id: UUID of the Arena problem.

    Returns:
        dict | None: The precomputed payload augmented with ``computed_at``
        (ISO-8601 string), or ``None`` if statistics have not been computed yet.
    """
    row = (
        await session.execute(
            select(
                _arena_problem_statistics.c.data,
                _arena_problem_statistics.c.computed_at,
            ).where(_arena_problem_statistics.c.problem_id == problem_id)
        )
    ).one_or_none()
    if row is None:
        return None
    payload: dict[str, Any] = dict(row.data)
    payload["computed_at"] = row.computed_at.isoformat()
    return payload


async def _resolve_solvers(session: AsyncSession, user_ids: set[str], viewer: ArenaUser) -> dict[str, _SolverIdentity]:
    """Resolve display name and profile-link permission for each solver id.

    One query answers both questions, because both need the same row. The
    snapshot's stored ``name`` is ``arena_users.nome`` frozen at the last rating
    cycle, so it must never be rendered as-is: an age-shielded solver is shown
    their username instead, exactly as on every other public surface.

    Args:
        session: Active async database session.
        user_ids: Candidate solver ids.
        viewer: The authenticated Arena user requesting the statistics.

    Returns:
        dict[str, _SolverIdentity]: One entry per id that still exists. An id
        that is absent from the result no longer has an account, and therefore
        no date of birth to age -- the caller must fail closed on it rather than
        fall back to the snapshot's stored legal name.
    """
    if not user_ids:
        return {}
    statement = select(
        _arena_users.c.id,
        _arena_users.c.nome,
        _arena_users.c.username,
        _arena_users.c.dta_nascimento,
        _arena_users.c.full_name_public,
        _arena_users.c.ativo,
        _arena_users.c.public_profile,
        _arena_users.c.ranking_visible,
    ).where(_arena_users.c.id.in_(user_ids))
    resolved: dict[str, _SolverIdentity] = {}
    for row in (await session.execute(statement)).all():
        identity = resolve_display_identity(
            user_id=row.id,
            full_name=row.nome,
            username=row.username,
            date_of_birth=row.dta_nascimento,
            full_name_public=row.full_name_public,
            public_profile=row.public_profile,
            ranking_visible=row.ranking_visible,
        )
        resolved[row.id] = _SolverIdentity(
            display_name=identity.display_name,
            # The same predicate as the public-profile route, so a link is
            # emitted exactly when that route would answer 200.
            viewable=can_view_public_profile(
                ativo=row.ativo,
                public_profile=row.public_profile,
                ranking_visible=row.ranking_visible,
                date_of_birth=row.dta_nascimento,
                viewer_role=viewer.role,
            ),
        )
    return resolved


async def get_problem_statistics_for_viewer(
    session: AsyncSession,
    problem_id: str,
    viewer: ArenaUser,
    profile_url_for: Callable[[str], str],
) -> dict[str, Any]:
    """Return the statistics payload decorated for one viewer.

    Adds ``computed_at_display`` and, on each non-null ``first_solver`` /
    ``last_solver``, ``solved_at_display`` (both in the viewer's timezone) plus
    ``profile_url`` when the viewer is allowed to open that solver's public
    profile.

    It also **replaces** each solver's ``name``. The snapshot stores
    ``arena_users.nome`` as frozen by the last rating cycle, and rendering that
    would publish an age-shielded solver's legal name -- a name they never chose
    to publish -- to every logged-in visitor. The name is re-resolved through
    the age shield here rather than in the snapshot, because the snapshot is
    written by the rating worker in ``shared/`` and the shield is Arena product
    policy that must not leak into a cross-module service.

    A solver whose account no longer exists gets ``UNKNOWN_SOLVER_NAME`` and no
    link: with no row there is no date of birth, so publishing the stored name
    would be a guess, and this fails closed instead.

    Args:
        session: Active async database session.
        problem_id: UUID of the Arena problem.
        viewer: The authenticated Arena user requesting the statistics.
        profile_url_for: Builds the public-profile URL for a user id.

    Returns:
        dict: The decorated payload, or ``{}`` when no snapshot exists yet.
    """
    stats = await get_problem_statistics(session, problem_id)
    if stats is None:
        return {}
    stats["computed_at_display"] = format_user_datetime(datetime.fromisoformat(str(stats["computed_at"])), viewer)

    solvers = [stats.get(key) for key in _SOLVER_KEYS]
    solver_ids = {solver["user_id"] for solver in solvers if solver}
    resolved = await _resolve_solvers(session, solver_ids, viewer)
    for solver in solvers:
        if not solver:
            continue
        solver["solved_at_display"] = format_user_datetime(datetime.fromisoformat(str(solver["solved_at"])), viewer)
        identity = resolved.get(solver["user_id"])
        solver["name"] = identity.display_name if identity else UNKNOWN_SOLVER_NAME
        if identity is not None and identity.viewable:
            solver["profile_url"] = profile_url_for(solver["user_id"])
    return stats
