#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the animator public feed endpoints.

Uses the root conftest ``engine``/``session``/``uberadmin`` fixtures. The app is
wired with a session factory on the test engine so the routes read the committed
seed data (helpers in ``_feed_seed``) through the animator Core service.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator.routes.public import router as public_router
from animator.services.contest_feed_service import build_snapshot_response, load_enabled_contest
from tests.animator._feed_seed import (
    make_contest,
    make_site,
    make_user,
    seed_dataset,
)
from web.models.site import Site
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


def _build_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.include_router(public_router)
    return app


class _QueryCounter:
    """Counts SQL statements executed on the engine within a context."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._sync_engine = engine.sync_engine
        self.count = 0

    def _on_execute(self, *args: object) -> None:
        self.count += 1

    def __enter__(self) -> _QueryCounter:
        event.listen(self._sync_engine, "after_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self._sync_engine, "after_cursor_execute", self._on_execute)


async def test_meta_enabled_returns_payload(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin, slug="route-meta")
    await seed_dataset(session, contest, uberadmin, teams=2, problems=2, submissions=2)
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/c/route-meta/meta")

    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "route-meta"
    assert [p["label"] for p in body["problems"]] == ["A", "B"]
    assert body["sites"][0]["team_count"] == 2
    # No secret-bearing fields leak into the payload.
    assert "secret" not in str(body).lower()
    assert "password" not in str(body).lower()


async def test_snapshot_enabled_returns_payload(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin, slug="route-snap")
    await seed_dataset(session, contest, uberadmin, teams=2, problems=1, submissions=2)
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/c/route-snap/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["contest_id"] == contest.id
    assert body["version"] == body["generated_at"]
    assert len(body["standings"]) == 2


async def test_snapshot_filters_teams_to_the_validated_site_scope(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """A site scoreboard contains only teams assigned to that contest site."""
    contest = await make_contest(session, uberadmin, slug="route-site-snap")
    north = await make_site(session, contest, sitename="North")
    south = await make_site(session, contest, sitename="South")
    north_team = make_user(contest, uberadmin, "north-team", site_id=north.id)
    south_team = make_user(contest, uberadmin, "south-team", site_id=south.id)
    session.add_all([north_team, south_team])
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(f"/c/route-site-snap/snapshot?scope={north.id}")

    assert response.status_code == 200
    assert [row["team_id"] for row in response.json()["standings"]] == [north_team.id]


async def test_snapshot_global_medals_follow_the_contest_cutoffs(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The global scoreboard bands rows by the contest's own cutoffs."""
    contest = await make_contest(session, uberadmin, slug="route-global-medals")
    contest.global_gold_cutoff = 1
    contest.global_silver_cutoff = 2
    contest.global_bronze_cutoff = 2
    await seed_dataset(session, contest, uberadmin, teams=4, problems=2, submissions=6)
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/c/route-global-medals/snapshot?scope=global")

    assert response.status_code == 200
    standings = response.json()["standings"]
    by_rank = {row["rank"]: row["medal"] for row in standings}
    assert by_rank[1] == "gold"
    # Silver and bronze share cutoff 2, and gold is tried first: rank 2 is
    # silver, and bronze covers nobody.
    assert by_rank[2] == "silver"
    assert all(row["medal"] is None for row in standings if row["rank"] > 2)


async def test_snapshot_reports_no_medals_when_global_cutoffs_are_unset(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """An unconfigured contest keeps today's behavior: no medals anywhere."""
    contest = await make_contest(session, uberadmin, slug="route-no-medals")
    await seed_dataset(session, contest, uberadmin, teams=2, problems=1, submissions=2)
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/c/route-no-medals/snapshot?scope=global")

    assert response.status_code == 200
    assert all(row["medal"] is None for row in response.json()["standings"])


async def test_site_snapshot_medals_use_the_site_cutoffs_not_the_contest_ones(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """A site scoreboard bands by its own cutoffs, ignoring the global ones."""
    contest = await make_contest(session, uberadmin, slug="route-site-medals")
    # Global medals stop at rank 1; the seeded site's default cutoffs (1/2/3)
    # reach rank 3, so a site row past rank 1 proves which set was applied.
    contest.global_gold_cutoff = 1
    contest.global_silver_cutoff = 1
    contest.global_bronze_cutoff = 1
    await seed_dataset(session, contest, uberadmin, teams=4, problems=2, submissions=6)
    await session.commit()
    site_id = (await session.execute(select(Site.id).where(Site.contest_id == contest.id))).scalar_one()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/c/route-site-medals/snapshot?scope={site_id}")

    assert response.status_code == 200
    by_rank = {row["rank"]: row["medal"] for row in response.json()["standings"]}
    assert by_rank[1] == "gold"
    assert by_rank[2] == "silver"


async def test_site_snapshot_without_cutoffs_is_refused(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The service enforces the site-scope invariant instead of documenting it.

    A site's cutoffs are NOT NULL, so their absence is a caller mistake. Falling
    back to the contest-wide bands would show a site the wrong podium and
    defaulting to none would silently blank it, so the service refuses outright.
    """
    contest = await make_contest(session, uberadmin, slug="site-snap-no-cutoffs")
    site = await make_site(session, contest, sitename="North")
    session.add(make_user(contest, uberadmin, "north-team", site_id=site.id))
    await session.commit()

    record = await load_enabled_contest(session, "site-snap-no-cutoffs")
    assert record is not None
    with pytest.raises(ValueError, match="requires the site's medal cutoffs"):
        await build_snapshot_response(session, record, site_id=site.id)


@pytest.mark.parametrize("suffix", ["meta", "snapshot"])
async def test_missing_and_disabled_are_indistinguishable(
    session: AsyncSession, uberadmin: UberAdmin, suffix: str
) -> None:
    await make_contest(session, uberadmin, slug="route-disabled", animator_enabled=False)
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        disabled = await client.get(f"/c/route-disabled/{suffix}")
        missing = await client.get(f"/c/route-unknown/{suffix}")

    assert disabled.status_code == missing.status_code == 404
    assert disabled.json() == missing.json()


async def test_meta_query_count_is_bounded(session: AsyncSession, uberadmin: UberAdmin) -> None:
    small = await make_contest(session, uberadmin, slug="meta-small")
    await seed_dataset(session, small, uberadmin, teams=1, problems=1, submissions=1)
    large = await make_contest(session, uberadmin, slug="meta-large")
    await seed_dataset(session, large, uberadmin, teams=8, problems=6, submissions=40)
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with _QueryCounter(engine) as small_counter:
            await client.get("/c/meta-small/meta")
        with _QueryCounter(engine) as large_counter:
            await client.get("/c/meta-large/meta")

    # contest (dependency) + problems + site team-counts + sites = 4, independent of data size.
    assert small_counter.count == large_counter.count == 4


async def test_snapshot_query_count_is_bounded(session: AsyncSession, uberadmin: UberAdmin) -> None:
    small = await make_contest(session, uberadmin, slug="snap-small")
    await seed_dataset(session, small, uberadmin, teams=1, problems=1, submissions=1)
    large = await make_contest(session, uberadmin, slug="snap-large")
    await seed_dataset(session, large, uberadmin, teams=8, problems=6, submissions=40)
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with _QueryCounter(engine) as small_counter:
            await client.get("/c/snap-small/snapshot")
        with _QueryCounter(engine) as large_counter:
            await client.get("/c/snap-large/snapshot")

    # contest (dependency) + teams + problems + submissions/judgments = 4, independent of data size.
    assert small_counter.count == large_counter.count == 4


async def test_site_snapshot_query_count_is_bounded(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A site snapshot costs the global four plus exactly the scope resolution.

    Scope resolution loads the contest's sites (team counts + sites = 2). The
    selected site's medal cutoffs are carried on the resolved scope from there,
    so the snapshot builder never loads the sites a second time to reach them.
    """
    small = await make_contest(session, uberadmin, slug="site-snap-small")
    small_site = await make_site(session, small, sitename="Small campus")
    session.add(make_user(small, uberadmin, "small-team", site_id=small_site.id))
    large = await make_contest(session, uberadmin, slug="site-snap-large")
    large_site = await make_site(session, large, sitename="Large campus")
    session.add_all([make_user(large, uberadmin, f"large-team{index}", site_id=large_site.id) for index in range(8)])
    await session.commit()

    app = _build_app(session.bind)  # type: ignore[arg-type]
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with _QueryCounter(engine) as small_counter:
            await client.get(f"/c/site-snap-small/snapshot?scope={small_site.id}")
        with _QueryCounter(engine) as large_counter:
            await client.get(f"/c/site-snap-large/snapshot?scope={large_site.id}")

    assert small_counter.count == large_counter.count == 6
