#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Web ``site_service`` animator wrappers.

These verify the thin Web boundary over the shared animator access service:
medal update returns the refreshed ``Site``, secret creation returns the
plaintext once, listing hides the digest, scope-separated resolution, and
revocation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from web.models.contest import Contest
from web.models.site import Site
from web.models.users import UberAdmin
from web.services import site_service


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
async def test_update_site_medals_returns_refreshed_site(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The wrapper writes and returns the same site with new values."""
    contest = await _make_contest(session, uberadmin, "wrap-medals")
    site = await _make_site(session, contest, "Campus A")

    returned = await site_service.update_site_medals(session, site, 1, 3, 5)
    assert returned is site
    assert (site.gold_cutoff, site.silver_cutoff, site.bronze_cutoff) == (1, 3, 5)


@pytest.mark.asyncio
async def test_create_and_list_site_secret(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Creating a site secret returns plaintext; listing hides the digest."""
    contest = await _make_contest(session, uberadmin, "wrap-create")
    site = await _make_site(session, contest, "Campus C")

    token = await site_service.create_site_secret(session, site, "Operador C")
    assert token

    entries = await site_service.list_site_secrets(session, contest, site.id)
    assert len(entries) == 1
    assert entries[0].label == "Operador C"
    assert not hasattr(entries[0], "secret_digest")


@pytest.mark.asyncio
async def test_get_site_by_secret_scope_separation(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A site secret resolves a Site; a global secret does not."""
    contest = await _make_contest(session, uberadmin, "wrap-scope-site")
    site = await _make_site(session, contest, "Campus D")

    site_token = await site_service.create_site_secret(session, site, "Site")
    global_token = await site_service.create_global_secret(session, contest, "Global")

    resolved_site = await site_service.get_site_by_secret(session, contest.id, site_token)
    assert resolved_site is not None
    assert resolved_site.id == site.id

    # A global token is not a site-scoped credential.
    assert await site_service.get_site_by_secret(session, contest.id, global_token) is None


@pytest.mark.asyncio
async def test_get_contest_by_global_secret_scope_separation(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A global secret resolves the Contest; a site secret does not."""
    contest = await _make_contest(session, uberadmin, "wrap-scope-global")
    site = await _make_site(session, contest, "Campus E")

    global_token = await site_service.create_global_secret(session, contest, "Global")
    site_token = await site_service.create_site_secret(session, site, "Site")

    resolved = await site_service.get_contest_by_global_secret(session, contest.id, global_token)
    assert resolved is not None
    assert resolved.id == contest.id

    # A site token is not a global control credential.
    assert await site_service.get_contest_by_global_secret(session, contest.id, site_token) is None


@pytest.mark.asyncio
async def test_delete_site_secret_removes_it(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """delete_site_secret revokes the credential so it no longer resolves."""
    contest = await _make_contest(session, uberadmin, "wrap-delete")
    site = await _make_site(session, contest, "Campus F")
    token = await site_service.create_site_secret(session, site, "Operador F")

    entries = await site_service.list_site_secrets(session, contest, site.id)
    assert len(entries) == 1
    secret_id = entries[0].id

    assert await site_service.delete_site_secret(session, contest, secret_id) is True

    assert await site_service.list_site_secrets(session, contest, site.id) == []
    assert await site_service.get_site_by_secret(session, contest.id, token) is None


@pytest.mark.asyncio
async def test_list_site_secrets_missing_site_returns_empty(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Listing secrets for an unknown site id yields an empty list."""
    contest = await _make_contest(session, uberadmin, "wrap-missing-site")
    assert await site_service.list_site_secrets(session, contest, "does-not-exist") == []


@pytest.mark.asyncio
async def test_list_site_secrets_rejects_cross_contest_site(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A site from another contest discloses no credentials under this contest."""
    contest_a = await _make_contest(session, uberadmin, "wrap-list-a")
    contest_b = await _make_contest(session, uberadmin, "wrap-list-b")
    site_b = await _make_site(session, contest_b, "Campus B")
    await site_service.create_site_secret(session, site_b, "Operador B")

    # Listing site_b's secrets while administering contest_a must return nothing.
    assert await site_service.list_site_secrets(session, contest_a, site_b.id) == []
    # And the credential is still there for its own contest.
    assert len(await site_service.list_site_secrets(session, contest_b, site_b.id)) == 1


@pytest.mark.asyncio
async def test_delete_site_secret_rejects_cross_contest(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Revoking another contest's secret by id is refused and leaves it intact."""
    contest_a = await _make_contest(session, uberadmin, "wrap-del-a")
    contest_b = await _make_contest(session, uberadmin, "wrap-del-b")
    site_b = await _make_site(session, contest_b, "Campus B")
    token = await site_service.create_site_secret(session, site_b, "Operador B")
    secret_id = (await site_service.list_site_secrets(session, contest_b, site_b.id))[0].id

    # Attempt revocation while administering contest_a: no-op.
    assert await site_service.delete_site_secret(session, contest_a, secret_id) is False

    # The secret survives and still resolves under its own contest.
    assert len(await site_service.list_site_secrets(session, contest_b, site_b.id)) == 1
    assert await site_service.get_site_by_secret(session, contest_b.id, token) is not None
