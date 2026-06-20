#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service for Arena class membership and registration requests.

Covers direct assignment/removal of users, listing the active members of a
class (with their current rating), self-service registration requests, and
teacher/admin decisions on those requests. Membership is recorded as a dated
status history; a same-day status flip overwrites that day's row.

All functions take an explicit ``AsyncSession`` and never commit; the caller
owns the transaction boundary (``session.flush()`` is used internally).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_classes import ArenaClass, ArenaClassMembership, ArenaClassRegistrationRequest
from arena.services.arena_class_service import (
    ArenaClassNotFoundError,
    ArenaClassPermissionError,
    ArenaClassValidationError,
    _assert_teacher_or_admin,
)
from shared.db_schema.arena import arena_class_memberships, arena_users
from shared.enumerations import (
    ArenaClassMembershipStatus,
    ArenaClassRegistrationStatus,
    ArenaRole,
)


@dataclass(frozen=True)
class ClassMemberRow:
    """A currently-active member of a class.

    Attributes:
        user_id: UUID of the member.
        name: Display name of the member.
        email: Normalised email of the member.
        user_rating: Member's current cached rating.
        registered_on: Date the active membership took effect.
    """

    user_id: str
    name: str
    email: str
    user_rating: int
    registered_on: date


async def _load_class(session: AsyncSession, class_id: str) -> ArenaClass:
    """Load a class or raise ``ArenaClassNotFoundError``."""
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaClassNotFoundError("Class does not exist.")
    return arena_class


async def _set_membership_status(
    session: AsyncSession,
    *,
    class_id: str,
    user_id: str,
    status: ArenaClassMembershipStatus,
    on_date: date,
) -> None:
    """Upsert the dated membership-status row for ``on_date``.

    Keyed by ``(class_id, user_id, event_date)`` so a same-day status flip
    overwrites that day's row, keeping only the last situation for the day.
    """
    existing = await session.get(ArenaClassMembership, (class_id, user_id, on_date))
    if existing is not None:
        existing.status = status.value
    else:
        session.add(
            ArenaClassMembership(
                class_id=class_id,
                user_id=user_id,
                event_date=on_date,
                status=status.value,
            )
        )
    await session.flush()


async def is_active_member(session: AsyncSession, *, class_id: str, user_id: str) -> bool:
    """Return ``True`` when the user has an active membership in the class."""
    max_date = await session.scalar(
        select(func.max(arena_class_memberships.c.event_date)).where(
            arena_class_memberships.c.class_id == class_id,
            arena_class_memberships.c.user_id == user_id,
        )
    )
    if max_date is None:
        return False
    status = await session.scalar(
        select(arena_class_memberships.c.status).where(
            arena_class_memberships.c.class_id == class_id,
            arena_class_memberships.c.user_id == user_id,
            arena_class_memberships.c.event_date == max_date,
        )
    )
    return status == ArenaClassMembershipStatus.ACTIVE.value


async def _validate_users_exist(session: AsyncSession, user_ids: list[str]) -> None:
    """Raise ``ArenaClassValidationError`` if any user id does not exist."""
    if not user_ids:
        raise ArenaClassValidationError("No users provided.")
    found = set((await session.execute(select(arena_users.c.id).where(arena_users.c.id.in_(user_ids)))).scalars().all())
    missing = set(user_ids) - found
    if missing:
        raise ArenaClassValidationError(f"Unknown user(s): {', '.join(sorted(missing))}")


async def assign_users(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    user_ids: list[str],
    on_date: date,
) -> int:
    """Assign one or more users to a class as ACTIVE members.

    Only the assigned teacher or an admin may assign users.

    Args:
        session: Active async database session.
        actor_id: Acting user's id.
        actor_role: Acting user's role.
        class_id: Target class id.
        user_ids: Users to enrol.
        on_date: Date the ACTIVE status takes effect.

    Returns:
        The number of users assigned.

    Raises:
        ArenaClassNotFoundError: When the class does not exist.
        ArenaClassPermissionError: When the actor may not assign users.
        ArenaClassValidationError: When a user id is unknown or none provided.
    """
    arena_class = await _load_class(session, class_id)
    _assert_teacher_or_admin(teacher_id=arena_class.teacher_id, actor_id=actor_id, actor_role=actor_role)
    unique_ids = list(dict.fromkeys(user_ids))
    await _validate_users_exist(session, unique_ids)
    for user_id in unique_ids:
        await _set_membership_status(
            session,
            class_id=class_id,
            user_id=user_id,
            status=ArenaClassMembershipStatus.ACTIVE,
            on_date=on_date,
        )
    return len(unique_ids)


async def remove_users(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    user_ids: list[str],
    on_date: date,
) -> int:
    """Remove one or more users from a class (mark REMOVED on ``on_date``).

    The assigned teacher or an admin may remove anyone; any other user may only
    remove themselves (``user_ids == [actor_id]``).

    Args:
        session: Active async database session.
        actor_id: Acting user's id.
        actor_role: Acting user's role.
        class_id: Target class id.
        user_ids: Users to remove.
        on_date: Date the REMOVED status takes effect.

    Returns:
        The number of users removed.

    Raises:
        ArenaClassNotFoundError: When the class does not exist.
        ArenaClassPermissionError: When the actor may not remove the given users.
        ArenaClassValidationError: When no users are provided.
    """
    arena_class = await _load_class(session, class_id)
    unique_ids = list(dict.fromkeys(user_ids))
    if not unique_ids:
        raise ArenaClassValidationError("No users provided.")
    is_privileged = actor_role == ArenaRole.ARENA_ADMIN or actor_id == arena_class.teacher_id
    if not is_privileged and unique_ids != [actor_id]:
        raise ArenaClassPermissionError("Users may only remove themselves from a class.")
    for user_id in unique_ids:
        await _set_membership_status(
            session,
            class_id=class_id,
            user_id=user_id,
            status=ArenaClassMembershipStatus.REMOVED,
            on_date=on_date,
        )
    return len(unique_ids)


async def list_class_members(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
) -> list[ClassMemberRow]:
    """List the currently-active members of a class with their current rating.

    Allowed for the assigned teacher, an admin, or a currently-active member.

    Args:
        session: Active async database session.
        actor_id: Acting user's id.
        actor_role: Acting user's role.
        class_id: Target class id.

    Returns:
        Active members ordered by name.

    Raises:
        ArenaClassNotFoundError: When the class does not exist.
        ArenaClassPermissionError: When the actor may not view the members.
    """
    arena_class = await _load_class(session, class_id)
    is_privileged = actor_role == ArenaRole.ARENA_ADMIN or actor_id == arena_class.teacher_id
    if not is_privileged and not await is_active_member(session, class_id=class_id, user_id=actor_id):
        raise ArenaClassPermissionError("Only the teacher, an admin, or a member may view members.")

    latest = (
        select(
            arena_class_memberships.c.user_id.label("user_id"),
            func.max(arena_class_memberships.c.event_date).label("max_date"),
        )
        .where(arena_class_memberships.c.class_id == class_id)
        .group_by(arena_class_memberships.c.user_id)
        .subquery()
    )
    stmt = (
        select(
            arena_users.c.id,
            arena_users.c.nome,
            arena_users.c.email_normalizado,
            func.coalesce(arena_users.c.user_rating, 0),
            arena_class_memberships.c.event_date,
        )
        .select_from(
            arena_class_memberships.join(
                latest,
                (arena_class_memberships.c.user_id == latest.c.user_id)
                & (arena_class_memberships.c.event_date == latest.c.max_date),
            ).join(arena_users, arena_class_memberships.c.user_id == arena_users.c.id)
        )
        .where(
            arena_class_memberships.c.class_id == class_id,
            arena_class_memberships.c.status == ArenaClassMembershipStatus.ACTIVE.value,
        )
        .order_by(arena_users.c.nome.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        ClassMemberRow(
            user_id=user_id,
            name=name,
            email=email,
            user_rating=int(rating),
            registered_on=event_date,
        )
        for user_id, name, email, rating, event_date in rows
    ]


async def request_registration(
    session: AsyncSession,
    *,
    user_id: str,
    class_id: str,
) -> ArenaClassRegistrationRequest:
    """Create a PENDING registration request for a class.

    Any registered user may request a class that allows self-registration.
    Rejected if the class forbids self-registration, the user is already an active
    member, or a pending request already exists for the class.

    Args:
        session: Active async database session.
        user_id: Requesting user id.
        class_id: Target class id.

    Returns:
        The created ``ArenaClassRegistrationRequest``.

    Raises:
        ArenaClassNotFoundError: When the class does not exist.
        ArenaClassValidationError: When self-registration is disabled, the user is
            already a member, or a request is pending.
    """
    arena_class = await _load_class(session, class_id)
    if arena_class.teacher_id == user_id:
        raise ArenaClassValidationError("The class teacher cannot request registration in their own class.")
    if not arena_class.allow_self_registration:
        raise ArenaClassValidationError("This class does not allow self-registration.")
    if await is_active_member(session, class_id=class_id, user_id=user_id):
        raise ArenaClassValidationError("User is already a member of this class.")
    existing = await session.scalar(
        select(func.count())
        .select_from(ArenaClassRegistrationRequest.__table__)
        .where(
            ArenaClassRegistrationRequest.__table__.c.class_id == class_id,
            ArenaClassRegistrationRequest.__table__.c.user_id == user_id,
            ArenaClassRegistrationRequest.__table__.c.status == ArenaClassRegistrationStatus.PENDING.value,
        )
    )
    if existing:
        raise ArenaClassValidationError("A pending request already exists for this class.")
    request = ArenaClassRegistrationRequest(
        class_id=class_id,
        user_id=user_id,
        status=ArenaClassRegistrationStatus.PENDING.value,
    )
    session.add(request)
    await session.flush()
    return request


async def decide_registration(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    request_id: str,
    approve: bool,
    on_date: date,
    reason: str | None = None,
) -> ArenaClassRegistrationRequest:
    """Approve or deny a pending registration request.

    Only the assigned teacher of the request's class or an admin may decide.
    Approving also creates an ACTIVE membership effective on ``on_date``.

    Args:
        session: Active async database session.
        actor_id: Deciding user's id.
        actor_role: Deciding user's role.
        request_id: The registration request id.
        approve: ``True`` to approve, ``False`` to deny.
        on_date: Date the ACTIVE membership takes effect when approving.
        reason: Optional free-text reason stored when denying (ignored on
            approval); truncated to 256 characters.

    Returns:
        The updated ``ArenaClassRegistrationRequest``.

    Raises:
        ArenaClassNotFoundError: When the request does not exist.
        ArenaClassPermissionError: When the actor may not decide the request.
        ArenaClassValidationError: When the request is not pending.
    """
    request = await session.get(ArenaClassRegistrationRequest, request_id)
    if request is None:
        raise ArenaClassNotFoundError("Registration request does not exist.")
    if request.status != ArenaClassRegistrationStatus.PENDING.value:
        raise ArenaClassValidationError("Registration request is not pending.")
    arena_class = await _load_class(session, request.class_id)
    _assert_teacher_or_admin(teacher_id=arena_class.teacher_id, actor_id=actor_id, actor_role=actor_role)

    request.status = (
        ArenaClassRegistrationStatus.APPROVED.value if approve else ArenaClassRegistrationStatus.DENIED.value
    )
    request.decided_at = datetime.now(tz=UTC)
    request.decided_by_id = actor_id
    if approve:
        request.denial_reason = None
    else:
        clean_reason = (reason or "").strip()
        request.denial_reason = clean_reason[:256] if clean_reason else None
    if approve:
        await _set_membership_status(
            session,
            class_id=request.class_id,
            user_id=request.user_id,
            status=ArenaClassMembershipStatus.ACTIVE,
            on_date=on_date,
        )
    await session.flush()
    return request
