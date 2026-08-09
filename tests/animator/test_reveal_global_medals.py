#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Medal cutoffs for the contest-global reveal ceremony.

A global ceremony used to be defined as the scope without medals. It now carries
the contest's own global cutoffs when they are configured, so these tests pin the
three things that behavior turns on: where a global ceremony's cutoffs come from,
that an unconfigured contest still produces no medals (today's behavior, and what
every ceremony recorded before this change looks like), and that the cutoffs are
*snapshotted* at creation rather than read live — which is why adopting a
settings change requires ``start-reveal`` with ``restart=true``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.reveal_session import MedalCutoffs, RevealSessionState
from animator.services.reveal_engine import start, step
from animator.services.reveal_loader import initialize_reveal_session
from animator.services.reveal_projection import compute_reveal_standings, medal_for_rank
from shared.db_schema import contests
from tests.animator._reveal_seed import load, seed_ceremony
from web.models.users import UberAdmin


async def _set_global_cutoffs(
    session: AsyncSession,
    contest_id: str,
    gold: int | None,
    silver: int | None,
    bronze: int | None,
) -> None:
    """Write the contest's global cutoffs directly, as the Web admin page would."""
    await session.execute(
        update(contests)
        .where(contests.c.id == contest_id)
        .values(global_gold_cutoff=gold, global_silver_cutoff=silver, global_bronze_cutoff=bronze)
    )
    await session.commit()


@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [(1, 2, None), (1, None, 3), (None, 2, 3), (1, None, None), (None, 2, None), (None, None, 3)],
    ids=["missing-bronze", "missing-silver", "missing-gold", "gold-only", "silver-only", "bronze-only"],
)
def test_from_optional_rejects_a_partial_triple(gold: int | None, silver: int | None, bronze: int | None) -> None:
    """Only an entirely empty triple means "no medals".

    A partial triple cannot be stored (the CHECK forbids it), so seeing one means
    the row was written out of band. Reading it as unconfigured would silently
    blank the medals on both surfaces with nothing to say why, so it raises.
    """
    with pytest.raises(ValueError, match="all set or all unset"):
        MedalCutoffs.from_optional(gold, silver, bronze)


def test_from_optional_accepts_an_empty_or_complete_triple() -> None:
    """The two valid shapes still map as before."""
    assert MedalCutoffs.from_optional(None, None, None) is None
    assert MedalCutoffs.from_optional(1, 2, 3) == MedalCutoffs(gold=1, silver=2, bronze=3)


@pytest.mark.asyncio
async def test_global_ceremony_adopts_the_contest_cutoffs(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    await _set_global_cutoffs(session, fixture.contest_id, 1, 2, 3)

    state = initialize_reveal_session(await load(session, fixture.slug, None))

    assert state.site_id is None
    assert state.site_name is None
    assert state.medal_cutoffs == MedalCutoffs(gold=1, silver=2, bronze=3)


@pytest.mark.asyncio
async def test_global_ceremony_without_configured_cutoffs_has_no_medals(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """An unconfigured contest keeps the pre-change behavior exactly."""
    fixture = await seed_ceremony(session, uberadmin)

    state = initialize_reveal_session(await load(session, fixture.slug, None))

    assert state.medal_cutoffs is None
    assert medal_for_rank(state.medal_cutoffs, 1) is None


@pytest.mark.asyncio
async def test_site_ceremony_still_uses_its_own_cutoffs(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A site ceremony is unaffected by the contest-level values."""
    fixture = await seed_ceremony(session, uberadmin)
    await _set_global_cutoffs(session, fixture.contest_id, 5, 6, 7)

    state = initialize_reveal_session(await load(session, fixture.slug, fixture.site_a))

    # Campus A is seeded with 1/2/3.
    assert state.medal_cutoffs == MedalCutoffs(gold=1, silver=2, bronze=3)


@pytest.mark.asyncio
async def test_global_ceremony_awards_medals_by_rank(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The projection bands global standings once cutoffs exist."""
    fixture = await seed_ceremony(session, uberadmin)
    await _set_global_cutoffs(session, fixture.contest_id, 1, 2, 3)
    dataset = await load(session, fixture.slug, None)

    state = start(dataset, initialize_reveal_session(dataset)).state
    standings = compute_reveal_standings(dataset, state)
    bands = [medal_for_rank(state.medal_cutoffs, row.rank) for row in standings]

    assert bands[0] == "gold"
    assert any(band is not None for band in bands[1:])


@pytest.mark.asyncio
async def test_a_stored_global_state_without_cutoffs_still_loads(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Forward compatibility: an old global payload is read, not refused.

    The state version was deliberately not bumped, because new code reads every
    old payload unchanged — a global ceremony recorded before global medals
    existed simply carries ``medal_cutoffs=None`` and shows no medals.
    """
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, None)
    old_payload = initialize_reveal_session(dataset).to_payload_json()

    restored = RevealSessionState.from_payload_json(old_payload)

    assert restored.medal_cutoffs is None
    assert restored.state_version == 3


@pytest.mark.asyncio
async def test_cutoffs_are_snapshotted_and_adopted_only_by_restart(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A configuration change reaches a live ceremony only through a rebuild.

    Reading cutoffs live would let an admin reshuffle the medal bands under an
    operator mid-ceremony. Instead the session keeps what it was created with,
    and the operator adopts a change with Start over (``restart=true``), which
    rebuilds the state from the database.
    """
    fixture = await seed_ceremony(session, uberadmin)
    await _set_global_cutoffs(session, fixture.contest_id, 1, 2, 3)
    dataset = await load(session, fixture.slug, None)
    live = step(dataset, start(dataset, initialize_reveal_session(dataset)).state).state

    await _set_global_cutoffs(session, fixture.contest_id, None, None, None)

    # The running ceremony is untouched: it holds its own snapshot, and stepping
    # it again re-reads nothing from the contest row.
    assert live.medal_cutoffs == MedalCutoffs(gold=1, silver=2, bronze=3)
    assert step(dataset, live).state.medal_cutoffs == MedalCutoffs(gold=1, silver=2, bronze=3)

    # Rebuilding from the database is what adopts the change.
    rebuilt = initialize_reveal_session(await load(session, fixture.slug, None))
    assert rebuilt.medal_cutoffs is None
