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
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator.routes.public import router as public_router
from tests.animator._feed_seed import (
    make_contest,
    make_site,
    make_user,
    seed_dataset,
)
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
