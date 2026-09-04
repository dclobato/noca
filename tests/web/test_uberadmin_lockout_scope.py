#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The per-contest scope of the UberAdmin login unlock (``/uberadmin/lockouts``).

``web``/``contest-login`` is keyed on ``{contest_id}:{username}``, which is what
makes "release ``interif``'s ``admin`` and leave ``contest1``'s locked"
expressible at all. These tests pin the half of that the operator sees:

- the scope is **chosen**, never defaulted: a typed login with no scope
  unlocks nothing, and so does one naming a contest that is not on offer
- a scoped unlock leaves every other contest's lock standing
- an unknown username is still unlockable, because the scope is what builds
  the identifier -- a failed login against a typo minted a bucket no account
  row can reproduce
- the audit row names the scope, so an entry releasing one contest cannot be
  read as one that released the whole deployment
- an explicit event hash needs no scope, and a request mixing the two without
  one is refused here rather than by the template's ``required``
- the status panel labels each ``contest-login`` row with its contest

The route-level helpers are shared with ``test_uberadmin_lockouts.py`` so the
two modules cannot drift on how a page is built, seeded, or posted to.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.auth_rate_limit import hash_identifier
from tests.conftest import _make_user
from tests.web.test_uberadmin_lockouts import (
    _PAGE,
    _audit,
    _client,
    _locked,
    _post,
    _second_contest,
    _seed,
    _setup,
)
from web.config import settings
from web.models.contest import Contest
from web.models.users import UberAdmin

_LOGIN = "team042"


async def _two_locked_contests(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> tuple[Contest, object, object, str]:
    """Two contests whose identically-named team is locked in both."""
    other = _second_contest(session, uberadmin)
    await session.flush()
    _make_user(session, running_contest, uberadmin, _LOGIN, "Team 42", RoleEnum.TEAM)
    _make_user(session, other, uberadmin, _LOGIN, "Team 42 again", RoleEnum.TEAM)
    await session.flush()
    app, valkey, token = await _setup(session, uberadmin)
    _seed(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:{_LOGIN}")
    _seed(valkey, module="web", action="contest-login", identifier=f"{other.id}:{_LOGIN}")
    return other, app, valkey, token


@pytest.mark.asyncio
async def test_unlocking_one_contest_leaves_the_other_locked(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> None:
    """The capability the issue exists for: release one contest, hold the rest."""
    other, app, valkey, token = await _two_locked_contests(session, uberadmin, running_contest)

    response = await _post(app, token, f"{_PAGE}/unlock-account", identifier=_LOGIN, contest_scope=other.id)

    assert response.status_code == 303
    assert not _locked(valkey, module="web", action="contest-login", identifier=f"{other.id}:{_LOGIN}")
    assert _locked(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:{_LOGIN}"), (
        "the contest the operator did not name keeps its lock"
    )
    audit = await _audit(session, "unlock_account")
    assert audit[0]["target_id"] == f"{_LOGIN} in Other Contest (other-contest) (1 contest user)", (
        "the audit row names the scope, so it cannot be read as a deployment-wide release"
    )


@pytest.mark.asyncio
async def test_a_typed_login_without_a_scope_unlocks_nothing(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> None:
    """Enforced server-side, not by the HTML ``required``: this POST bypasses the form."""
    other, app, valkey, token = await _two_locked_contests(session, uberadmin, running_contest)

    response = await _post(app, token, f"{_PAGE}/unlock-account", identifier=_LOGIN)

    assert response.status_code == 303
    assert _locked(valkey, module="web", action="contest-login", identifier=f"{other.id}:{_LOGIN}")
    assert _locked(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:{_LOGIN}")
    assert await _audit(session, "unlock_account") == [], "nothing happened, so nothing is audited"


@pytest.mark.asyncio
async def test_a_scope_that_is_not_on_offer_unlocks_nothing(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> None:
    """A contest id is validated against the same list the form offered."""
    other, app, valkey, token = await _two_locked_contests(session, uberadmin, running_contest)

    response = await _post(app, token, f"{_PAGE}/unlock-account", identifier=_LOGIN, contest_scope="not-a-contest")

    assert response.status_code == 303
    assert _locked(valkey, module="web", action="contest-login", identifier=f"{other.id}:{_LOGIN}")
    assert _locked(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:{_LOGIN}")
    assert await _audit(session, "unlock_account") == []


@pytest.mark.asyncio
async def test_a_scope_unlocks_a_username_no_account_carries(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> None:
    """A failed login against a typo minted ``{contest_id}:<typo>``; the scope rebuilds it.

    No account row can produce that identifier, so the explicit scope is the
    only thing that can name the bucket -- which is exactly why it is asked for
    rather than derived from what the username matches.
    """
    app, valkey, token = await _setup(session, uberadmin)
    _seed(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:tema042")

    response = await _post(
        app, token, f"{_PAGE}/unlock-account", identifier="tema042", contest_scope=running_contest.id
    )

    assert response.status_code == 303
    assert not _locked(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:tema042")
    audit = await _audit(session, "unlock_account")
    assert audit[0]["target_id"].endswith("(no account)"), "the audit row is honest about matching nothing"


@pytest.mark.asyncio
async def test_an_event_hash_needs_no_scope_but_a_typed_login_beside_it_does(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> None:
    """The security-event link's one-click unlock, and the refusal that keeps it honest."""
    other, app, valkey, token = await _two_locked_contests(session, uberadmin, running_contest)
    digest = hash_identifier(f"{other.id}:{_LOGIN}", secret=settings.JWT_SECRET_KEY)
    assert digest is not None

    mixed = await _post(app, token, f"{_PAGE}/unlock-account", identifier=_LOGIN, identifier_hash=digest)
    assert mixed.status_code == 303
    assert _locked(valkey, module="web", action="contest-login", identifier=f"{other.id}:{_LOGIN}"), (
        "a login typed beside a hash still needs a scope, so nothing was cleared"
    )

    hash_only = await _post(app, token, f"{_PAGE}/unlock-account", identifier_hash=digest)

    assert hash_only.status_code == 303
    assert not _locked(valkey, module="web", action="contest-login", identifier=f"{other.id}:{_LOGIN}")
    assert _locked(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:{_LOGIN}"), (
        "one exact bucket, in one contest"
    )
    audit = await _audit(session, "unlock_account")
    assert (audit[0]["target_type"], audit[0]["target_id"]) == ("identifier_hash", digest[:12])


@pytest.mark.asyncio
async def test_the_status_panel_names_the_contest_of_each_scoped_lock(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> None:
    """Two contests' locks are two rows, each naming its contest rather than being guessed."""
    other, app, valkey, token = await _two_locked_contests(session, uberadmin, running_contest)

    async with _client(app, token) as client:
        page = await client.get(_PAGE, params={"identifier": _LOGIN})

    assert page.status_code == 200
    assert page.text.count("web/contest-login") == 2, "one row per contest, not one collapsed row"
    assert "Other Contest (other-contest)" in page.text
    assert f"{running_contest.contest_name} ({running_contest.login_slug})" in page.text


@pytest.mark.asyncio
async def test_the_scope_select_offers_all_contests_and_every_active_one(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> None:
    """No blank default: the placeholder carries no value, so a scope is always stated."""
    other = _second_contest(session, uberadmin)
    await session.flush()
    app, _valkey, token = await _setup(session, uberadmin)

    async with _client(app, token) as client:
        page = await client.get(_PAGE)

    assert '<option value="" selected disabled>Select…</option>' in page.text
    assert '<option value="all">All contests</option>' in page.text
    assert f'<option value="{other.id}">Other Contest (other-contest)</option>' in page.text
    assert f'value="{running_contest.id}"' in page.text
