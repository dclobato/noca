#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public ceremony-scope resolution for spectator routes.

A spectator has no credential, so the ceremony they watch is selected by a
**validated query value**: ``?scope=global`` or ``?scope=<site id>``. That value
is deliberately *not* an operator token and grants nothing — it only chooses
which already-public ceremony to read.

Two properties this module exists to guarantee:

- **The scope is resolved against the contest, never trusted.** A site id is
  accepted only when that site belongs to the resolved contest, so the query
  value cannot reach across contests.
- **``global`` stays reserved.** The literal is the contest-global ceremony and
  is never treated as a site id — the same reservation
  :meth:`animator.services.reveal_session_store.RevealSessionStore.scope_for`
  enforces on the write side, so the spectator and operator views of one
  ceremony always address the same Valkey key and channel.

Rejection is a bare ``404``, raised by the caller: an unknown site, a site of
another contest, and a syntactically absurd value are indistinguishable from an
unknown slug, so scope cannot be used to enumerate sites.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import ContestRecord
from animator.models.reveal_session import MedalCutoffs
from animator.services.contest_queries import load_sites
from shared.reveal_schema import GLOBAL_SCOPE

__all__ = ["PublicScope", "resolve_public_scope"]


@dataclass(frozen=True)
class PublicScope:
    """One resolved spectator scope.

    Attributes:
        site_id: The site being watched, or ``None`` for the global ceremony.
        site_name: Display name of ``site_id``; ``None`` iff global.
        canonical: The Valkey scope component — the site id, or ``"global"``.
        medal_cutoffs: The cutoffs in force for this scope — the selected site's,
            or ``None`` for the global scope, where the caller falls back to the
            contest's own global cutoffs.

    ``medal_cutoffs`` is carried here rather than looked up again downstream:
    resolving a site scope already loads every site, so a second ``load_sites``
    in the snapshot builder would repeat two queries (including the team counts
    it does not need) for data this resolution had in hand and discarded.
    """

    site_id: str | None
    site_name: str | None
    canonical: str
    medal_cutoffs: MedalCutoffs | None = None


async def resolve_public_scope(
    session: AsyncSession,
    contest: ContestRecord,
    scope: str,
) -> PublicScope | None:
    """Resolve a spectator ``scope`` query value against one contest.

    Args:
        session: Active database session.
        contest: The already-resolved animator-enabled contest.
        scope: The raw query value (``"global"`` or a site id).

    Returns:
        The resolved scope, or ``None`` when the value names no site of this
        contest. The caller turns ``None`` into the uniform bare ``404``.
    """
    value = scope.strip()
    if value == GLOBAL_SCOPE:
        return PublicScope(site_id=None, site_name=None, canonical=GLOBAL_SCOPE)
    if not value:
        return None
    site = next((row for row in await load_sites(session, contest.id) if row.id == value), None)
    if site is None:
        return None
    return PublicScope(
        site_id=site.id,
        site_name=site.sitename,
        canonical=site.id,
        medal_cutoffs=MedalCutoffs(
            gold=site.gold_cutoff,
            silver=site.silver_cutoff,
            bronze=site.bronze_cutoff,
        ),
    )
