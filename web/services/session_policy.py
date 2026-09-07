#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single expression of the single-session, single-IP team login policy.

Once a contest has started, a user whose ``allow_concurrent_login`` is false
holds **one** session, from **one** client IP. Before the start nothing is
enforced, and after the end nothing is enforced either.

Every surface that authenticates a Web request consults this module rather than
re-deriving the rule, because the rule has four parts that must agree: who it
applies to, when it applies, whether the session is the current one, and
whether the request comes from the bound address. Three separate actor
resolvers and a heartbeat each spelling that out is three chances for one of
them to disagree, and the failure mode of disagreement is a session that is
refused on one page and accepted on another.

Two properties are load-bearing.

**The epoch is bumped by every login, including logins before the contest
starts.** It is only *enforced* from the start instant onward, so a pre-start
login is never refused and never checked -- but by the time enforcement begins,
each pre-start session already carries a distinct epoch and only the newest one
matches the row. Without the unconditional bump, several pre-start sessions
would share one epoch and the survivor would be decided by whichever session
happened to make the first request after the start, a race a forgotten browser
tab polling in the background can win against the machine at the venue.

**Every state change is one conditional statement, never a read-then-write.**
Two requests can observe the same unlocked user, and two logins can read the
same epoch; resolving that in Python would let both act on what they saw. The
binding is an ``UPDATE`` guarded on *both* ``locked_ip IS NULL`` and the epoch
the request was authorized against -- the first so two racing requests cannot
both bind, the second so a request a concurrent login has already superseded
cannot bind on its way out -- and the bump is an ``UPDATE ... SET epoch = epoch
+ 1 RETURNING`` evaluated by the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Row, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes
from sqlalchemy.sql.elements import ColumnElement

from shared.db_schema import users as users_t
from shared.enumerations import RoleEnum
from web.models import Contest, User

#: Roles the policy never applies to, whatever their flag says.
#:
#: The exemption is by **current role**, not by the flag's default. An
#: administrator whose flag was cleared by the contest-wide bulk toggle must
#: still be exempt, and the failure mode of getting this wrong is locking the
#: organisers out of their own running contest.
EXEMPT_ROLES: frozenset[RoleEnum] = frozenset({RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF})

#: The epoch every row lands at, and holds until its owner's first login.
#:
#: A token with no epoch claim was minted before the claim existed, and is
#: honoured only while the row still reads this value. See `evaluate_session`.
_PRE_DEPLOYMENT_EPOCH = 0

#: The name of the token claim carrying the epoch a session was minted at.
#:
#: It lives here rather than beside the other token claims because it is the
#: policy's vocabulary: the login stamps it, the resolvers read it, and this
#: module is the only thing that knows what it means.
SESSION_EPOCH_CLAIM = "session_epoch"


#: How many times a governed login re-attempts its guarded write.
#:
#: Only one writer can force a retry -- an administrator's `clear_ip_lock`
#: landing between the guarded write and the read that diagnoses it -- so a
#: second attempt is already generous and a third is the margin.
_AUTHORIZE_LOGIN_ATTEMPTS = 3


class SessionLoginRefusedError(Exception):
    """A login was refused by the policy, with the password already proven.

    Deliberately **not** a ``ValueError``: this is not an authentication
    failure. Sharing the credentials path would count it against the login
    throttle and report it as a wrong password, which would leave a team locked
    out with no idea why. The login route catches this base class, so a refusal
    added later cannot fall through to the credentials branch by omission.
    """


class SessionIpLockedError(SessionLoginRefusedError):
    """A login was refused because the user's sessions are bound elsewhere.

    Attributes:
        locked_ip: The address the user's sessions are bound to.
    """

    def __init__(self, locked_ip: str) -> None:
        super().__init__(f"Sessions are bound to {locked_ip}")
        self.locked_ip = locked_ip


class SessionBindingContendedError(SessionLoginRefusedError):
    """A login gave up because the user's binding kept moving underneath it.

    Reachable only when an administrator's release lands between the guarded
    write and the read that diagnoses it, repeatedly. Refusing is the safe
    direction: the alternative is an unguarded bump that would supersede
    whichever session actually holds the binding.
    """

    def __init__(self) -> None:
        super().__init__("The session binding changed while this login was being authorized")


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """What the policy committed for one accepted login.

    Attributes:
        session_epoch: The epoch the database committed for this login, which
            the caller stamps into the token it mints.
        locked_ip: The address the user is bound to afterwards, or ``None``
            when the user is not bound at all.
    """

    session_epoch: int
    locked_ip: str | None


class SessionVerdict(StrEnum):
    """What the policy says about one authenticated request."""

    ALLOW = "allow"
    #: A newer login for this user superseded this session.
    STALE_SESSION = "stale_session"
    #: This user's sessions are bound to a different client address.
    FOREIGN_ADDRESS = "foreign_address"


def policy_may_apply(user: User) -> bool:
    """Return whether the policy could ever govern ``user``, contest aside.

    Args:
        user: The authenticated contest user.

    Returns:
        ``True`` when the user is non-exempt and has opted out of concurrent
        logins -- the half of :func:`policy_applies` that needs no contest.

    Notes:
        This exists so a caller holding only the user can tell that no contest
        needs loading at all, which is the common case: every staff member, and
        every team on a deployment that never turned the flag off. It is a
        *half* of the predicate rather than a second copy of it --
        :func:`policy_applies` is defined in terms of this function, so the two
        cannot drift into disagreeing about who is exempt.

        The exemption is by **current role**. An administrator whose flag was
        cleared by the contest-wide bulk toggle must still be exempt, and the
        failure mode of getting this wrong is locking the organisers out of
        their own running contest.
    """
    if user.role in EXEMPT_ROLES:
        return False
    return not user.allow_concurrent_login


def policy_applies(user: User, contest: Contest) -> bool:
    """Return whether the policy governs ``user`` on ``contest`` right now.

    Args:
        user: The authenticated contest user.
        contest: The contest the request is scoped to.

    Returns:
        ``True`` when the user is non-exempt, has opted out of concurrent
        logins, and the contest is running.

    Notes:
        ``Contest.is_running`` is exactly the window wanted here -- at or after
        the start instant and not past the end. After the end the policy stops
        applying, so a binding left behind is inert; it only matters again if
        the same contest is re-run by moving its start time, which is what the
        opt-in ``session_lock_reaper`` releases in advance.
    """
    return policy_may_apply(user) and contest.is_running


async def evaluate_session(
    session: AsyncSession,
    user: User,
    contest: Contest,
    *,
    token_epoch: int | None,
    client_ip: str | None,
) -> SessionVerdict:
    """Decide whether one authenticated request may proceed, binding if needed.

    Args:
        session: Database session. Any binding this performs is committed
            before returning, so it survives a request that later fails.
        user: The authenticated contest user, loaded by the caller.
        contest: The contest the request is scoped to.
        token_epoch: The ``session_epoch`` claim carried by the request token,
            or ``None`` for a token minted before the claim existed.
        client_ip: The proxy-corrected client address, or ``None`` when it
            cannot be determined.

    Returns:
        The verdict. Only ``ALLOW`` may proceed.

    Notes:
        A ``token_epoch`` of ``None`` is accepted **only while the stored epoch
        is still zero**, which is exactly "this user has not logged in since the
        feature deployed". Every session live at the moment this ships carries
        such a token, and refusing them all would sign out an entire running
        contest on a rolling deploy -- but accepting them unconditionally would
        be worse, because a claimless token would then outlive the login that
        was supposed to supersede it and never be invalidated at all. The
        migration lands every row at zero and the first login bumps it, so the
        grace period closes by itself, per user, at the first login.

        A ``client_ip`` of ``None`` never *binds* -- there is nothing to bind --
        but is refused against an existing binding, since the request cannot be
        shown to come from the bound address.
    """
    if not policy_applies(user, contest):
        return SessionVerdict.ALLOW

    expected_epoch = user.session_epoch
    if token_epoch is None:
        if expected_epoch != _PRE_DEPLOYMENT_EPOCH:
            return SessionVerdict.STALE_SESSION
    elif token_epoch != expected_epoch:
        return SessionVerdict.STALE_SESSION

    if user.locked_ip is not None:
        return SessionVerdict.ALLOW if client_ip == user.locked_ip else SessionVerdict.FOREIGN_ADDRESS

    if client_ip is None:
        return SessionVerdict.ALLOW

    return await bind_client_ip(session, user, client_ip, expected_epoch=expected_epoch)


async def bind_client_ip(
    session: AsyncSession,
    user: User,
    client_ip: str,
    *,
    expected_epoch: int,
) -> SessionVerdict:
    """Bind ``user`` to ``client_ip``, and say whether this request may proceed.

    Args:
        session: Database session. The binding is committed before returning.
        user: The user to bind. Its in-memory attributes are updated to match
            whatever the database now holds.
        client_ip: The address to bind to.
        expected_epoch: The epoch this request was authorized against. The bind
            applies only while the row still holds it.

    Returns:
        ``ALLOW`` when this request holds the binding afterwards,
        ``FOREIGN_ADDRESS`` when another address won it, and ``STALE_SESSION``
        when a login superseded this session while the bind was in flight.

    Notes:
        The write carries **both** guards, and each closes a different race.
        ``locked_ip IS NULL`` stops two requests that each observed an unbound
        user from both binding; the loser reads back the winner's address and is
        judged against it rather than overwriting it.

        ``session_epoch = :expected_epoch`` stops a request that was authorized
        against an epoch a concurrent login has since bumped. The epoch checked
        by :func:`evaluate_session` comes from an ORM-loaded row, so between that
        check and this write another login can supersede the session -- and
        without this guard the superseded request would still bind the address
        and carry on, which is precisely the invalidation the epoch exists to
        perform. It cannot be done by re-reading first: that is the read-then-
        write this module exists to avoid.

        The commit is deliberate. This runs before the route body, when the
        session holds nothing else, and a binding that vanished because the
        request it rode on failed would be re-raced by the next request.
    """
    bound_at = datetime.now(UTC)
    statement = (
        update(users_t)
        .where(
            users_t.c.id == user.id,
            users_t.c.locked_ip.is_(None),
            users_t.c.session_epoch == expected_epoch,
        )
        .values(locked_ip=client_ip, locked_at=bound_at)
        .returning(users_t.c.locked_ip, users_t.c.locked_at)
    )
    won = (await session.execute(statement)).one_or_none()
    if won is not None:
        await session.commit()
        _sync_binding(user, won.locked_ip, won.locked_at)
        return SessionVerdict.ALLOW

    # The write matched nothing. Which of the two guards refused it decides the
    # verdict, so read the row back rather than guessing from what we held.
    current = (
        await session.execute(
            select(users_t.c.locked_ip, users_t.c.locked_at, users_t.c.session_epoch).where(users_t.c.id == user.id)
        )
    ).one_or_none()
    await session.commit()
    if current is None:
        # The row was deleted under us. Nothing can authorize this request.
        return SessionVerdict.STALE_SESSION
    if current.session_epoch != expected_epoch:
        return SessionVerdict.STALE_SESSION
    if current.locked_ip is None:
        # Unbound at the same epoch, which the service paths cannot produce --
        # `clear_ip_lock` bumps the epoch. Leave the user unbound for the next
        # request to bind rather than inventing a binding here.
        return SessionVerdict.ALLOW
    _sync_binding(user, current.locked_ip, current.locked_at)
    return SessionVerdict.ALLOW if current.locked_ip == client_ip else SessionVerdict.FOREIGN_ADDRESS


def _sync_binding(user: User, locked_ip: str | None, locked_at: datetime | None) -> None:
    """Make the in-memory user match the row without marking it dirty.

    A plain assignment would leave the attribute pending, and the next flush on
    this session would write it back -- over an admin's ``clear_ip_lock`` in the
    same request, for instance, silently restoring the binding that was just
    released. The value came *from* the database, so it is recorded as already
    committed rather than as a change to make.
    """
    attributes.set_committed_value(user, "locked_ip", locked_ip)
    attributes.set_committed_value(user, "locked_at", locked_at)


async def authorize_login(
    session: AsyncSession,
    user: User,
    contest: Contest,
    *,
    client_ip: str | None,
) -> LoginOutcome:
    """Mint this login's epoch, binding the address when the policy applies.

    Args:
        session: Session owning the caller's transaction. Nothing is committed
            here: the epoch must land with the login history row, so a login
            that fails afterwards does not supersede a session it never
            replaced.
        user: The user whose password has just been checked. Its in-memory
            attributes are updated to match whatever the database now holds.
        contest: The contest the login is scoped to.
        client_ip: The proxy-corrected client address, or ``None`` when it
            cannot be determined.

    Returns:
        The committed epoch and the address the user is bound to afterwards.

    Raises:
        SessionIpLockedError: If the user's sessions are bound to an address
            this login cannot show it came from, which no credential can
            override -- only an administrator's release can.
        SessionBindingContendedError: If the binding kept moving underneath the
            attempt. See :data:`_AUTHORIZE_LOGIN_ATTEMPTS`.
        ValueError: If the user row does not exist.

    Notes:
        **The bump is unconditional**, including before the contest starts and
        including for users the policy never governs. That is what gives each
        pre-start session a distinct epoch, so that when enforcement begins at
        the start instant only the newest login survives, rather than the
        session that happens to make the first request afterwards.

        **When the policy applies, the bump is guarded by the binding, in one
        statement.** Bumping first and checking the address second would let the
        epoch advance for a login the binding then refuses, superseding the
        team's working session on behalf of an attempt that was never allowed to
        start. That is true of the missing-address case as much as of the known
        one: an unbound row read into memory may have been bound by another
        transaction since, so the guard -- not the loaded attribute -- is what
        decides.

        A known address guards on ``locked_ip IS NULL OR locked_ip =
        :client_ip``, so a same-address relogin is accepted and still bumps -- a
        team whose browser crashed can sign back in. An address that cannot be
        determined guards on ``locked_ip IS NULL`` alone and binds nothing:
        there is nothing to bind, and against an existing binding the request
        cannot be shown to come from the bound seat, exactly as
        :func:`evaluate_session` refuses it.
    """
    if not policy_applies(user, contest):
        epoch = await bump_session_epoch(session, user.id)
        attributes.set_committed_value(user, "session_epoch", epoch)
        return LoginOutcome(session_epoch=epoch, locked_ip=user.locked_ip)

    for _ in range(_AUTHORIZE_LOGIN_ATTEMPTS):
        won = await _authorize_governed_login(session, user.id, client_ip)
        if won is not None:
            attributes.set_committed_value(user, "session_epoch", int(won.session_epoch))
            _sync_binding(user, won.locked_ip, won.locked_at)
            return LoginOutcome(session_epoch=int(won.session_epoch), locked_ip=won.locked_ip)

        # The write matched nothing, so read the row back to learn why rather
        # than reporting what we happened to hold, which another transaction may
        # already have replaced.
        current = (
            await session.execute(select(users_t.c.locked_ip, users_t.c.locked_at).where(users_t.c.id == user.id))
        ).one_or_none()
        if current is None:
            raise ValueError(f"Cannot authorize the login of an unknown user: {user.id!r}")
        if current.locked_ip is not None:
            _sync_binding(user, current.locked_ip, current.locked_at)
            raise SessionIpLockedError(current.locked_ip)
        # Bound when the write ran, unbound now: an administrator released the
        # lock in between. The login is entitled to proceed, so re-attempt it
        # rather than reporting a refusal the team can do nothing about -- and
        # never mistake this for a missing row, which would surface to the route
        # as a credentials failure and charge the throttle for a correct
        # password.
        _sync_binding(user, None, None)

    raise SessionBindingContendedError


async def _authorize_governed_login(session: AsyncSession, user_id: str, client_ip: str | None) -> Row[Any] | None:
    """Bump the epoch and bind, but only while the binding still permits it.

    Args:
        session: Database session. Nothing is committed here.
        user_id: The user being authorized.
        client_ip: The address to bind to, or ``None`` when it is unknown.

    Returns:
        The committed epoch and binding, or ``None`` when the guard refused the
        write -- because the row is bound elsewhere, or is gone.
    """
    guard: ColumnElement[bool] = users_t.c.locked_ip.is_(None)
    values: dict[str, Any] = {"session_epoch": users_t.c.session_epoch + 1}
    if client_ip is not None:
        guard = or_(guard, users_t.c.locked_ip == client_ip)
        values["locked_ip"] = func.coalesce(users_t.c.locked_ip, client_ip)
        values["locked_at"] = case((users_t.c.locked_ip.is_(None), datetime.now(UTC)), else_=users_t.c.locked_at)

    statement = (
        update(users_t)
        .where(users_t.c.id == user_id, guard)
        .values(**values)
        .returning(users_t.c.session_epoch, users_t.c.locked_ip, users_t.c.locked_at)
    )
    return (await session.execute(statement)).one_or_none()


async def bump_session_epoch(session: AsyncSession, user_id: str) -> int:
    """Advance a user's session epoch, superseding every session they hold.

    Args:
        session: Session owning the caller's transaction. Not committed here --
            the epoch must land with whatever the caller is doing, so a login
            that fails afterwards does not sign the user out of a session it
            never replaced.
        user_id: The user whose sessions are superseded.

    Returns:
        The new epoch, which the caller stamps into the token it mints.

    Raises:
        ValueError: If the user row does not exist.

    Notes:
        The increment is evaluated by the database, so two concurrent logins
        yield two distinct epochs and the token each mints carries the value its
        own statement committed -- never one computed from a value read before.
    """
    statement = (
        update(users_t)
        .where(users_t.c.id == user_id)
        .values(session_epoch=users_t.c.session_epoch + 1)
        .returning(users_t.c.session_epoch)
    )
    epoch = await session.scalar(statement)
    if epoch is None:
        raise ValueError(f"Cannot bump the session epoch of an unknown user: {user_id!r}")
    return int(epoch)


async def clear_ip_lock(session: AsyncSession, user_id: str) -> int:
    """Release a user's IP binding and supersede every session bound to it.

    Args:
        session: Session owning the caller's transaction. Not committed here, so
            the release lands with the audit row that records who ordered it.
        user_id: The user to release.

    Returns:
        The new session epoch.

    Raises:
        ValueError: If the user row does not exist.

    Notes:
        Clearing the address alone would leave every session that was bound to
        it still valid, and the first of them to make a request would simply
        re-bind the same address. The epoch bump is what makes the release mean
        "start again", which is the point of handing it to an operator whose
        team has changed seats or lost a DHCP lease.
    """
    statement = (
        update(users_t)
        .where(users_t.c.id == user_id)
        .values(locked_ip=None, locked_at=None, session_epoch=users_t.c.session_epoch + 1)
        .returning(users_t.c.session_epoch)
    )
    epoch = await session.scalar(statement)
    if epoch is None:
        raise ValueError(f"Cannot clear the IP lock of an unknown user: {user_id!r}")
    return int(epoch)
