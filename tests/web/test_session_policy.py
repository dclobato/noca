#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the single-session, single-IP team login policy.

The concurrency tests at the end are the reason this module exists as one
place rather than as a rule spelled out in each resolver. Two requests can
observe the same unlocked user and two logins can read the same epoch, so every
transition has to be decided by the database rather than in Python. These tests
drive two sessions against one database to show that it is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db_schema import users as users_t
from shared.enumerations import RoleEnum
from web.models import Contest, UberAdmin, User
from web.services import session_policy
from web.services.session_policy import (
    EXEMPT_ROLES,
    SessionBindingContendedError,
    SessionIpLockedError,
    SessionVerdict,
    authorize_login,
    bump_session_epoch,
    clear_ip_lock,
    evaluate_session,
    policy_applies,
)

_VENUE_IP = "203.0.113.10"
_HOME_IP = "198.51.100.4"


@pytest_asyncio.fixture
async def future_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """A contest that has not started yet (starts in an hour, lasts 2 h)."""
    contest = Contest(
        contest_name="Future Contest",
        contest_url="http://future.example.com",
        login_slug="future-contest",
        start_time=datetime.now(UTC) + timedelta(hours=1),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


def _restrict(user: User) -> User:
    """Opt one user into the policy, as an organiser's toggle would."""
    user.allow_concurrent_login = False
    return user


async def _locked_ip(session: AsyncSession, user_id: str) -> str | None:
    return await session.scalar(select(users_t.c.locked_ip).where(users_t.c.id == user_id))


# ---------------------------------------------------------------------------
# Who and when the policy applies to
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", sorted(EXEMPT_ROLES))
async def test_staff_are_exempt_by_role_not_by_their_flag(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, role: RoleEnum
) -> None:
    """The bulk toggle can clear an organiser's flag; the role must still exempt them.

    Getting this wrong locks the organisers out of their own running contest,
    which is why the exemption reads the current role rather than trusting the
    column's default.
    """
    staff = User(
        username=f"staff_{role.value.lower()}",
        fullname=f"{role.value} User",
        role=role,
        contest_id=running_contest.id,
        created_by_uberadmin_id=uberadmin.id,
        allow_concurrent_login=False,
    )
    staff.password = "TestPass1!"
    session.add(staff)
    await session.flush()

    assert policy_applies(staff, running_contest) is False


@pytest.mark.asyncio
async def test_a_team_that_kept_the_flag_is_untouched(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The policy is opt-in: the default-true flag means today's behaviour."""
    assert team_user.allow_concurrent_login is True
    assert policy_applies(team_user, running_contest) is False


@pytest.mark.asyncio
async def test_a_restricted_team_in_a_running_contest_is_governed(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    assert policy_applies(_restrict(team_user), running_contest) is True


@pytest.mark.asyncio
async def test_nothing_is_enforced_before_the_start(
    session: AsyncSession, future_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Warm-up, practice and credential checks stay unrestricted."""
    team = _restrict(
        User(
            username="early_team",
            fullname="Early Team",
            role=RoleEnum.TEAM,
            contest_id=future_contest.id,
            created_by_uberadmin_id=uberadmin.id,
        )
    )
    team.password = "TestPass1!"
    session.add(team)
    await session.flush()

    assert policy_applies(team, future_contest) is False


@pytest.mark.asyncio
async def test_nothing_is_enforced_after_the_end(
    session: AsyncSession, stopped_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Past the end the policy stops; the reaper clears the bindings it left."""
    team = _restrict(
        User(
            username="late_team",
            fullname="Late Team",
            role=RoleEnum.TEAM,
            contest_id=stopped_contest.id,
            created_by_uberadmin_id=uberadmin.id,
        )
    )
    team.password = "TestPass1!"
    session.add(team)
    await session.flush()

    assert policy_applies(team, stopped_contest) is False


# ---------------------------------------------------------------------------
# The per-request verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ungoverned_user_is_allowed_whatever_the_request_looks_like(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """A user outside the policy is never measured against epoch or address."""
    team_user.session_epoch = 7
    team_user.locked_ip = _VENUE_IP

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=1, client_ip=_HOME_IP)

    assert verdict is SessionVerdict.ALLOW


@pytest.mark.asyncio
async def test_a_superseded_session_is_rejected(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """A later login bumped the epoch, so this token names a session that is gone."""
    _restrict(team_user)
    team_user.session_epoch = 4

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=3, client_ip=_VENUE_IP)

    assert verdict is SessionVerdict.STALE_SESSION


@pytest.mark.asyncio
async def test_the_epoch_is_checked_before_the_address(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """A superseded session is refused even when it comes from the bound address.

    Order matters: the newest session is the one allowed, and coming from the
    right seat does not make an older tab current again.
    """
    _restrict(team_user)
    team_user.session_epoch = 4
    team_user.locked_ip = _VENUE_IP

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=3, client_ip=_VENUE_IP)

    assert verdict is SessionVerdict.STALE_SESSION


@pytest.mark.asyncio
async def test_a_token_minted_before_the_feature_is_accepted_until_the_next_login(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """A rolling deploy must not sign out an entire running contest.

    Every session live when this ships carries a token with no epoch claim, and
    the migration lands every row at epoch 0. Such a token is honoured while
    that is still true.
    """
    _restrict(team_user)
    assert team_user.session_epoch == 0

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=None, client_ip=_VENUE_IP)

    assert verdict is SessionVerdict.ALLOW
    assert await _locked_ip(session, team_user.id) == _VENUE_IP


@pytest.mark.asyncio
async def test_a_claimless_token_stops_working_once_the_user_logs_in_again(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The grace period closes per user, at that user's first login.

    Accepting a claimless token unconditionally would let it outlive the login
    meant to supersede it, and it would then never be invalidated at all -- the
    exact opposite of what the epoch is for. The stored epoch leaving zero is
    what says a login has happened since the deploy.
    """
    # Flushed before the refresh below, which would otherwise reload the row
    # and quietly undo the restriction this test is about.
    _restrict(team_user)
    await session.flush()

    await bump_session_epoch(session, team_user.id)
    await session.refresh(team_user)

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=None, client_ip=_VENUE_IP)

    assert verdict is SessionVerdict.STALE_SESSION
    assert await _locked_ip(session, team_user.id) is None


@pytest.mark.asyncio
async def test_the_first_request_binds_the_address(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    _restrict(team_user)

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_VENUE_IP)

    assert verdict is SessionVerdict.ALLOW
    assert await _locked_ip(session, team_user.id) == _VENUE_IP
    assert team_user.locked_ip == _VENUE_IP
    assert team_user.locked_at is not None


@pytest.mark.asyncio
async def test_the_bound_address_is_allowed_again(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_VENUE_IP)

    assert verdict is SessionVerdict.ALLOW


@pytest.mark.asyncio
async def test_another_address_is_refused(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    """A copied cookie must not work from anywhere; this is the whole point."""
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_HOME_IP)

    assert verdict is SessionVerdict.FOREIGN_ADDRESS


@pytest.mark.asyncio
async def test_an_unknown_address_is_refused_against_a_binding(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The request cannot be shown to come from the bound seat, so it is not."""
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=None)

    assert verdict is SessionVerdict.FOREIGN_ADDRESS


@pytest.mark.asyncio
async def test_an_unknown_address_binds_nothing(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """There is nothing to bind, so the user stays unbound rather than bound to junk."""
    _restrict(team_user)

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=None)

    assert verdict is SessionVerdict.ALLOW
    assert await _locked_ip(session, team_user.id) is None


# ---------------------------------------------------------------------------
# Concurrency: every transition is decided by the database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_requests_racing_to_bind_produce_one_winner(
    engine, session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """Both requests observed an unlocked user; only one may bind it.

    The guard is ``locked_ip IS NULL`` in the write itself, so the loser reads
    back the winner's address and is judged against it rather than overwriting
    it with its own.
    """
    _restrict(team_user)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as venue, factory() as home:
        venue_user = await venue.get(User, team_user.id)
        home_user = await home.get(User, team_user.id)
        assert venue_user is not None and home_user is not None
        # Both hold a row that says "unbound"; this is the racing observation.
        assert venue_user.locked_ip is None
        assert home_user.locked_ip is None

        venue_verdict = await evaluate_session(venue, venue_user, running_contest, token_epoch=0, client_ip=_VENUE_IP)
        home_verdict = await evaluate_session(home, home_user, running_contest, token_epoch=0, client_ip=_HOME_IP)

        assert venue_verdict is SessionVerdict.ALLOW
        assert home_verdict is SessionVerdict.FOREIGN_ADDRESS
        assert await _locked_ip(home, team_user.id) == _VENUE_IP


@pytest.mark.asyncio
async def test_a_binding_survives_the_request_that_made_it(
    engine, session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The bind is committed on its own, so a request that fails later cannot undo it.

    A binding rolled back with its request would simply be re-raced by the next
    one, which is the race this policy exists to settle once.
    """
    _restrict(team_user)
    await session.commit()
    user_id = team_user.id

    await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_VENUE_IP)
    await session.rollback()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as observer:
        assert await _locked_ip(observer, user_id) == _VENUE_IP


@pytest.mark.asyncio
async def test_the_epoch_bump_is_not_a_read_then_write(engine, session: AsyncSession, team_user: User) -> None:
    """A login must never mint an epoch computed from a value it read earlier.

    Here one login reads ``0``, a second login commits ``1`` underneath it, and
    the first then bumps. It must yield ``2`` -- if the increment were computed
    in Python from what it read, both would mint ``1`` and two live sessions
    would pass the same check.
    """
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as first, factory() as second:
        observed = await first.scalar(select(users_t.c.session_epoch).where(users_t.c.id == team_user.id))
        assert observed == 0
        # Release the read transaction so the interleaving is about the policy,
        # not about SQLite's writer lock.
        await first.rollback()

        second_epoch = await bump_session_epoch(second, team_user.id)
        await second.commit()

        first_epoch = await bump_session_epoch(first, team_user.id)
        await first.commit()

    assert second_epoch == 1
    assert first_epoch == 2


@pytest.mark.asyncio
async def test_sequential_logins_mint_distinct_epochs(session: AsyncSession, team_user: User) -> None:
    epochs = [await bump_session_epoch(session, team_user.id) for _ in range(3)]

    assert epochs == [1, 2, 3]


@pytest.mark.asyncio
async def test_clearing_the_lock_also_supersedes_the_bound_sessions(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """Clearing the address alone would let a bound session simply re-bind it.

    The epoch bump is what makes an operator's release mean "start again" for a
    team that has changed seats or lost a DHCP lease.
    """
    _restrict(team_user)
    await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_VENUE_IP)

    epoch = await clear_ip_lock(session, team_user.id)
    await session.commit()

    assert epoch == 1
    assert await _locked_ip(session, team_user.id) is None
    assert await session.scalar(select(users_t.c.locked_at).where(users_t.c.id == team_user.id)) is None

    await session.refresh(team_user)
    stale = await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_VENUE_IP)
    assert stale is SessionVerdict.STALE_SESSION


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [bump_session_epoch, clear_ip_lock])
async def test_an_unknown_user_is_an_error_not_a_silent_no_op(session: AsyncSession, operation) -> None:
    """A silent no-op here would mint a token for a session nothing supersedes."""
    with pytest.raises(ValueError, match="unknown user"):
        await operation(session, "does-not-exist")


@pytest.mark.asyncio
async def test_binding_leaves_the_user_object_clean(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The in-memory user must match the row without holding a pending change.

    A plain assignment after the commit would leave ``locked_ip`` dirty, and the
    next flush on this session would write it back -- over an admin's
    ``clear_ip_lock`` in the same request, silently restoring the binding that
    was just released.
    """
    _restrict(team_user)
    await session.commit()

    await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_VENUE_IP)

    assert team_user.locked_ip == _VENUE_IP
    assert team_user not in session.dirty


@pytest.mark.asyncio
async def test_a_bind_cannot_outrun_a_login_that_superseded_it(
    engine, session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """A request authorized against an epoch a login has since bumped binds nothing.

    The epoch `evaluate_session` checks comes from an ORM-loaded row, so a login
    can supersede the session between that check and the write. Without the
    epoch in the update predicate the superseded request would still bind the
    address and carry on -- exactly the invalidation the epoch exists to
    perform, defeated on its way out.
    """
    _restrict(team_user)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as browsing, factory() as logging_in:
        stale_user = await browsing.get(User, team_user.id)
        assert stale_user is not None
        assert stale_user.session_epoch == 0  # the epoch this request is authorized against

        # A second login lands first, superseding the session above.
        await bump_session_epoch(logging_in, team_user.id)
        await logging_in.commit()

        verdict = await evaluate_session(browsing, stale_user, running_contest, token_epoch=0, client_ip=_HOME_IP)

        assert verdict is SessionVerdict.STALE_SESSION
        assert await _locked_ip(browsing, team_user.id) is None


@pytest.mark.asyncio
async def test_a_bind_cannot_outrun_an_admin_release(
    engine, session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The same fence covers the administrator's release, which also bumps the epoch.

    An operator clearing a lock has decided the team starts again; a request
    already in flight must not land a binding on the far side of that decision.
    """
    _restrict(team_user)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as browsing, factory() as admin:
        stale_user = await browsing.get(User, team_user.id)
        assert stale_user is not None

        await clear_ip_lock(admin, team_user.id)
        await admin.commit()

        verdict = await evaluate_session(browsing, stale_user, running_contest, token_epoch=0, client_ip=_HOME_IP)

        assert verdict is SessionVerdict.STALE_SESSION
        assert await _locked_ip(browsing, team_user.id) is None


# ---------------------------------------------------------------------------
# The login transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_login_mints_a_new_epoch_even_before_the_start(
    session: AsyncSession, future_contest: Contest, uberadmin: UberAdmin
) -> None:
    """The unconditional bump is what makes the start cutover pick one session.

    Two pre-start logins that shared an epoch would both match the row when
    enforcement begins, and the survivor would be whichever made the first
    request afterwards -- a race a forgotten background tab can win.
    """
    team = User(
        username="team_prestart",
        fullname="Pre-start Team",
        role=RoleEnum.TEAM,
        contest_id=future_contest.id,
        created_by_uberadmin_id=uberadmin.id,
        allow_concurrent_login=False,
    )
    team.password = "TestPass1!"
    session.add(team)
    await session.flush()

    home = await authorize_login(session, team, future_contest, client_ip=_HOME_IP)
    venue = await authorize_login(session, team, future_contest, client_ip=_VENUE_IP)

    assert (home.session_epoch, venue.session_epoch) == (1, 2)
    assert await _locked_ip(session, team.id) is None, "nothing is bound before the start"


@pytest.mark.asyncio
async def test_the_first_login_of_a_running_contest_binds_the_address(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    _restrict(team_user)
    await session.flush()

    outcome = await authorize_login(session, team_user, running_contest, client_ip=_VENUE_IP)

    assert outcome.locked_ip == _VENUE_IP
    assert outcome.session_epoch == 1
    assert await _locked_ip(session, team_user.id) == _VENUE_IP
    assert team_user.locked_at is not None
    assert team_user not in session.dirty


@pytest.mark.asyncio
async def test_the_bound_seat_can_sign_in_again(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """A crashed browser must be recoverable, and the relogin still kicks the old tab."""
    first = await authorize_login(session, _restrict(team_user), running_contest, client_ip=_VENUE_IP)
    bound_at = team_user.locked_at

    second = await authorize_login(session, team_user, running_contest, client_ip=_VENUE_IP)

    assert second.session_epoch == first.session_epoch + 1
    assert second.locked_ip == _VENUE_IP
    assert team_user.locked_at == bound_at, "the original binding instant is not restamped"


@pytest.mark.asyncio
async def test_a_login_from_another_address_is_refused_and_supersedes_nothing(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The credential is proven and still refused, and the team keeps working.

    The refusal must leave the epoch alone: bumping it would sign the venue's
    session out on behalf of an attempt that was never allowed to start.
    """
    await authorize_login(session, _restrict(team_user), running_contest, client_ip=_VENUE_IP)

    with pytest.raises(SessionIpLockedError) as refused:
        await authorize_login(session, team_user, running_contest, client_ip=_HOME_IP)

    assert refused.value.locked_ip == _VENUE_IP
    epoch = await session.scalar(select(users_t.c.session_epoch).where(users_t.c.id == team_user.id))
    assert epoch == 1
    assert await _locked_ip(session, team_user.id) == _VENUE_IP


@pytest.mark.asyncio
async def test_an_unknown_address_cannot_prove_it_is_the_bound_one(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    await authorize_login(session, _restrict(team_user), running_contest, client_ip=_VENUE_IP)

    with pytest.raises(SessionIpLockedError):
        await authorize_login(session, team_user, running_contest, client_ip=None)


@pytest.mark.asyncio
async def test_an_unknown_address_binds_nothing_at_login(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    outcome = await authorize_login(session, _restrict(team_user), running_contest, client_ip=None)

    assert outcome.session_epoch == 1
    assert outcome.locked_ip is None
    assert await _locked_ip(session, team_user.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [False, True])
async def test_an_ungoverned_login_bumps_the_epoch_and_binds_nothing(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, restricted: bool
) -> None:
    """A judge with the flag cleared by the bulk toggle is still exempt by role."""
    staff = User(
        username=f"judge_{int(restricted)}",
        fullname="Judge",
        role=RoleEnum.JUDGE,
        contest_id=running_contest.id,
        created_by_uberadmin_id=uberadmin.id,
        allow_concurrent_login=not restricted,
    )
    staff.password = "TestPass1!"
    session.add(staff)
    await session.flush()

    outcome = await authorize_login(session, staff, running_contest, client_ip=_VENUE_IP)

    assert outcome.session_epoch == 1
    assert await _locked_ip(session, staff.id) is None


@pytest.mark.asyncio
async def test_two_simultaneous_first_logins_produce_one_binding(
    engine, session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """Both logins observed an unbound user; the database picks the seat.

    The loser is refused rather than merely unbound, and -- because the bump
    and the bind are one statement -- it does not advance the epoch on its way
    out, so the winner's freshly minted token stays valid.
    """
    _restrict(team_user)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as venue_session, factory() as home_session:
        venue_user = await venue_session.get(User, team_user.id)
        home_user = await home_session.get(User, team_user.id)
        assert venue_user is not None and home_user is not None

        venue = await authorize_login(venue_session, venue_user, running_contest, client_ip=_VENUE_IP)
        await venue_session.commit()

        with pytest.raises(SessionIpLockedError) as refused:
            await authorize_login(home_session, home_user, running_contest, client_ip=_HOME_IP)
        await home_session.rollback()

        assert refused.value.locked_ip == _VENUE_IP
        assert venue.session_epoch == 1
        epoch = await session.scalar(select(users_t.c.session_epoch).where(users_t.c.id == team_user.id))
        assert epoch == 1, "the refused login minted no epoch, so the winner's token still matches"


@pytest.mark.asyncio
async def test_a_login_with_no_address_cannot_bump_past_a_binding_made_meanwhile(
    engine, session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The missing-address path is guarded in SQL, not by the loaded attribute.

    A login whose client address cannot be determined loaded an unbound row; by
    the time it writes, the venue's login has bound it. An unguarded bump here
    would invalidate the token the venue just minted *and* mint one from an
    address that can never satisfy the per-request check -- the team would be
    signed out and unable to sign back in.
    """
    _restrict(team_user)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as anonymous, factory() as venue:
        stale_user = await anonymous.get(User, team_user.id)
        venue_user = await venue.get(User, team_user.id)
        assert stale_user is not None and venue_user is not None
        assert stale_user.locked_ip is None, "loaded before the venue bound it"

        venue_outcome = await authorize_login(venue, venue_user, running_contest, client_ip=_VENUE_IP)
        await venue.commit()

        with pytest.raises(SessionIpLockedError) as refused:
            await authorize_login(anonymous, stale_user, running_contest, client_ip=None)
        await anonymous.rollback()

        assert refused.value.locked_ip == _VENUE_IP
        epoch = await session.scalar(select(users_t.c.session_epoch).where(users_t.c.id == team_user.id))
        assert epoch == venue_outcome.session_epoch, "the venue's freshly minted token still matches the row"


@pytest.mark.asyncio
async def test_a_release_between_the_write_and_the_read_is_retried_not_reported(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlocked row read back after a missed write is not a missing one.

    An administrator clearing the lock in that window is precisely the case the
    team is waiting for. Reporting it as an unknown user would surface a correct
    password to the route as invalid credentials and charge the login throttle
    for it, so the attempt is retried instead.
    """
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    real_attempt = session_policy._authorize_governed_login
    calls = 0

    async def _release_between_the_write_and_the_read(*args: object, **kwargs: object) -> object:
        """Miss once, with the administrator's release landing in the window."""
        nonlocal calls
        calls += 1
        if calls == 1:
            async with factory() as admin:
                await clear_ip_lock(admin, team_user.id)
                await admin.commit()
            return None
        return await real_attempt(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session_policy, "_authorize_governed_login", _release_between_the_write_and_the_read)

    async with factory() as home:
        home_user = await home.get(User, team_user.id)
        assert home_user is not None

        outcome = await authorize_login(home, home_user, running_contest, client_ip=_HOME_IP)
        await home.commit()

    assert calls == 2, "the attempt was retried rather than reported"
    assert outcome.locked_ip == _HOME_IP, "the released lock is rebound to the seat that signed in"
    assert await _locked_ip(session, team_user.id) == _HOME_IP


@pytest.mark.asyncio
async def test_a_binding_that_never_settles_refuses_rather_than_bumping(
    session: AsyncSession, running_contest: Contest, team_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhausting the retries must not fall back to an unguarded bump.

    Refusing is the safe direction: a bump that ignored the binding would
    supersede whichever session actually holds it.
    """
    _restrict(team_user)
    await session.flush()

    async def _always_miss(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(session_policy, "_authorize_governed_login", _always_miss)

    with pytest.raises(SessionBindingContendedError):
        await authorize_login(session, team_user, running_contest, client_ip=_VENUE_IP)

    epoch = await session.scalar(select(users_t.c.session_epoch).where(users_t.c.id == team_user.id))
    assert epoch == 0


@pytest.mark.asyncio
async def test_an_unknown_user_cannot_be_authorized(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """A genuinely missing row is still an error, and must stay distinguishable."""
    _restrict(team_user)
    await session.flush()
    await session.execute(users_t.delete().where(users_t.c.id == team_user.id))

    with pytest.raises(ValueError, match="unknown user"):
        await authorize_login(session, team_user, running_contest, client_ip=_VENUE_IP)


# ---------------------------------------------------------------------------
# Mid-contest transitions of the flag (the #216 design's lifecycle rules)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turning_the_flag_on_releases_a_bound_team_at_once(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The design's rule: turning the flag *on* releases the user immediately.

    The policy simply stops applying, so a stored binding and a superseded
    epoch both become irrelevant in the same instant -- no request has to run
    and nothing has to be cleaned up first.
    """
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP
    team_user.session_epoch = 5
    await session.flush()

    team_user.allow_concurrent_login = True

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=1, client_ip=_HOME_IP)

    assert verdict is SessionVerdict.ALLOW, "a foreign address and a stale epoch are both ignored once released"


@pytest.mark.asyncio
async def test_turning_the_flag_off_binds_on_the_next_request(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The other half of the rule: turning it *off* binds on that user's next request.

    No re-login is required -- the binding is made by whichever request arrives
    first, exactly as it is for a session opened before the contest started.
    """
    assert team_user.allow_concurrent_login is True
    _restrict(team_user)
    await session.flush()

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_HOME_IP)

    assert verdict is SessionVerdict.ALLOW
    assert await _locked_ip(session, team_user.id) == _HOME_IP


@pytest.mark.asyncio
async def test_the_policy_enforces_whatever_binding_the_row_carries(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The rule does not ask how the address got there, which is why lifts clear it.

    Set the flag back and forth on the row alone -- as no service path does --
    and the stored address is enforced again. The guarantee that no stale
    binding survives a lift lives in the admin services
    (`set_contest_team_session_policy`, `update_user`), not here, and this test
    is what makes the division visible: keep it passing and remove the clearing,
    and the trap comes back.
    """
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP
    await session.flush()

    team_user.allow_concurrent_login = True  # lifted, on the row only
    await session.flush()
    team_user.allow_concurrent_login = False  # re-applied, binding never cleared
    await session.flush()

    verdict = await evaluate_session(session, team_user, running_contest, token_epoch=0, client_ip=_HOME_IP)

    assert verdict is SessionVerdict.FOREIGN_ADDRESS
