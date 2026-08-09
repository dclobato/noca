#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Schema tests for Animator access control and medal settings.

These retain the Phase 01 contracts for the ``contests.animator_enabled`` gate,
per-site medal cutoffs, and ``site_secrets`` while also covering the nullable,
all-or-nothing contest-global medal cutoff triple added for global scoreboards
and reveal ceremonies.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from web.models.contest import Contest
from web.models.site import Site
from web.models.site_secret import SiteSecret
from web.models.users import UberAdmin


async def _make_contest(session: AsyncSession, uberadmin: UberAdmin, slug: str) -> Contest:
    """Persist a minimal running contest and return it flushed."""
    contest = Contest(
        contest_name=f"Contest {slug}",
        contest_url=f"http://{slug}.example.com",
        login_slug=slug,
        start_time=datetime.now(UTC) - timedelta(minutes=30),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


async def _make_site(session: AsyncSession, contest: Contest, name: str) -> Site:
    """Persist a site under the given contest and return it flushed."""
    site = Site(
        sitename=name,
        sitename_normalized=name.lower(),
        contest_id=contest.id,
    )
    session.add(site)
    await session.flush()
    return site


@pytest.mark.asyncio
async def test_animator_enabled_defaults_false(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """An old-style contest migrates safely: the gate defaults to disabled."""
    contest = await _make_contest(session, uberadmin, "gate-default")

    assert contest.animator_enabled is False


@pytest.mark.asyncio
async def test_global_medal_cutoffs_default_to_unconfigured(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A contest starts with no global medals, so nothing needed backfilling."""
    contest = await _make_contest(session, uberadmin, "global-default")

    assert contest.global_gold_cutoff is None
    assert contest.global_silver_cutoff is None
    assert contest.global_bronze_cutoff is None


@pytest.mark.asyncio
async def test_global_medal_cutoffs_accept_a_full_ordered_triple(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """All three set, positive, and ordered satisfies the CHECK constraint."""
    contest = await _make_contest(session, uberadmin, "global-set")
    contest.global_gold_cutoff = 4
    contest.global_silver_cutoff = 8
    contest.global_bronze_cutoff = 12
    await session.flush()

    assert contest.global_bronze_cutoff == 12


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [
        (1, 2, None),
        (1, None, 3),
        (None, 2, 3),
        (1, None, None),
        (None, 2, None),
        (None, None, 3),
    ],
    ids=["missing-bronze", "missing-silver", "missing-gold", "gold-only", "silver-only", "bronze-only"],
)
async def test_global_medal_partial_triple_rejected(
    session: AsyncSession,
    uberadmin: UberAdmin,
    gold: int | None,
    silver: int | None,
    bronze: int | None,
) -> None:
    """The database itself refuses a partly configured triple.

    This is the case a naive CHECK would let through: a comparison against NULL
    evaluates to UNKNOWN, and a CHECK rejects only FALSE, so the constraint must
    assert ``IS NOT NULL`` explicitly on its configured branch.
    """
    contest = await _make_contest(session, uberadmin, f"global-partial-{gold}-{silver}-{bronze}")
    contest.global_gold_cutoff = gold
    contest.global_silver_cutoff = silver
    contest.global_bronze_cutoff = bronze
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [(5, 2, 3), (0, 2, 3), (1, 9, 3)],
    ids=["gold-above-silver", "gold-not-positive", "silver-above-bronze"],
)
async def test_global_medal_unordered_or_nonpositive_rejected(
    session: AsyncSession,
    uberadmin: UberAdmin,
    gold: int,
    silver: int,
    bronze: int,
) -> None:
    """Configured global cutoffs obey the same rules a site's do."""
    contest = await _make_contest(session, uberadmin, f"global-bad-{gold}-{silver}-{bronze}")
    contest.global_gold_cutoff = gold
    contest.global_silver_cutoff = silver
    contest.global_bronze_cutoff = bronze
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_site_medal_cutoffs_default(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A freshly created site carries the ordered 1/2/3 default cutoffs."""
    contest = await _make_contest(session, uberadmin, "cutoff-default")
    site = await _make_site(session, contest, "Campus A")

    assert site.gold_cutoff == 1
    assert site.silver_cutoff == 2
    assert site.bronze_cutoff == 3
    assert "style" not in site.__table__.c


@pytest.mark.asyncio
async def test_site_cutoffs_out_of_order_rejected(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Gold greater than silver violates the ordering CHECK constraint."""
    contest = await _make_contest(session, uberadmin, "cutoff-order")
    site = Site(
        sitename="Campus B",
        sitename_normalized="campus b",
        contest_id=contest.id,
        gold_cutoff=5,
        silver_cutoff=2,
        bronze_cutoff=3,
    )
    session.add(site)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_site_cutoff_non_positive_rejected(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A zero gold cutoff violates the positivity half of the CHECK constraint."""
    contest = await _make_contest(session, uberadmin, "cutoff-positive")
    site = Site(
        sitename="Campus C",
        sitename_normalized="campus c",
        contest_id=contest.id,
        gold_cutoff=0,
        silver_cutoff=2,
        bronze_cutoff=3,
    )
    session.add(site)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_global_secret_representable(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A contest-global control secret (site_id NULL) is representable."""
    contest = await _make_contest(session, uberadmin, "secret-global")
    secret = SiteSecret(
        contest_id=contest.id,
        site_id=None,
        secret_digest="a" * 64,
        label="Global",
    )
    session.add(secret)
    await session.flush()

    assert secret.site_id is None
    assert secret.contest_id == contest.id


@pytest.mark.asyncio
async def test_site_scoped_secret_representable(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A site-scoped operator secret (site_id set) is representable."""
    contest = await _make_contest(session, uberadmin, "secret-site")
    site = await _make_site(session, contest, "Campus D")
    secret = SiteSecret(
        contest_id=contest.id,
        site_id=site.id,
        secret_digest="b" * 64,
        label="Operador D",
    )
    session.add(secret)
    await session.flush()

    assert secret.site_id == site.id


def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
    """Turn on per-connection foreign-key enforcement for SQLite."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


@pytest.mark.asyncio
async def test_cross_contest_secret_fk_rejected(engine: AsyncEngine) -> None:
    """A secret cannot reference a site that belongs to a different contest.

    SQLite ignores ``PRAGMA foreign_keys`` inside an open transaction, so the
    pragma is installed on a fresh connection through a connect listener before
    any transaction begins. The schema already lives in the file-backed test
    database, so disposing the pool does not lose it.
    """
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    await engine.dispose()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as fk_session:
        uberadmin = UberAdmin(
            username="fk_uberadmin",
            fullname="FK UberAdmin",
            email_normalizado="fk-ua@test.example.com",
        )
        uberadmin.password = "TestPass1!"
        fk_session.add(uberadmin)
        await fk_session.flush()

        contest_a = await _make_contest(fk_session, uberadmin, "fk-contest-a")
        contest_b = await _make_contest(fk_session, uberadmin, "fk-contest-b")
        site_b = await _make_site(fk_session, contest_b, "Campus B-only")

        secret = SiteSecret(
            contest_id=contest_a.id,
            site_id=site_b.id,
            secret_digest="c" * 64,
            label="Cross-contest",
        )
        fk_session.add(secret)
        with pytest.raises(IntegrityError):
            await fk_session.flush()
        await fk_session.rollback()


@pytest.mark.asyncio
async def test_short_secret_digest_rejected(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A digest shorter than the fixed 64 characters violates the length CHECK."""
    contest = await _make_contest(session, uberadmin, "secret-short")
    secret = SiteSecret(
        contest_id=contest.id,
        site_id=None,
        secret_digest="e" * 32,
        label="Too short",
    )
    session.add(secret)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_duplicate_secret_digest_rejected(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Two secrets with the same (contest_id, secret_digest) violate uniqueness."""
    contest = await _make_contest(session, uberadmin, "secret-dup")
    first = SiteSecret(
        contest_id=contest.id,
        site_id=None,
        secret_digest="d" * 64,
        label="First",
    )
    second = SiteSecret(
        contest_id=contest.id,
        site_id=None,
        secret_digest="d" * 64,
        label="Second",
    )
    session.add_all([first, second])
    with pytest.raises(IntegrityError):
        await session.flush()
