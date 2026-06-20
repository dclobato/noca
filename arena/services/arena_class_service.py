#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core Arena class service: shared types, exceptions, and CRUD operations.

Membership mutation and registration requests live in
:mod:`arena.services.arena_class_membership_service`.
Listing and discovery queries live in
:mod:`arena.services.arena_class_query_service`.
Detail DTO helpers and management queries live in
:mod:`arena.services.arena_class_detail_service`.

All functions take an explicit ``AsyncSession`` and never commit; the caller
owns the transaction boundary (``session.flush()`` is used internally).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_classes import ArenaClass
from arena.models.arena_users import ArenaUser
from shared.db_schema.arena import arena_class_memberships
from shared.enumerations import (
    ArenaClassMembershipStatus,
    ArenaRole,
)


class ArenaClassServiceError(Exception):
    """Base error for Arena class services."""


class ArenaClassNotFoundError(ArenaClassServiceError):
    """Raised when a referenced class does not exist."""


class ArenaClassPermissionError(ArenaClassServiceError):
    """Raised when the actor is not allowed to perform the operation."""


class ArenaClassValidationError(ArenaClassServiceError):
    """Raised when input validation fails."""


@dataclass(frozen=True)
class ClassSummary:
    """Summary row for a class shown in discovery listings.

    Attributes:
        class_id: UUID of the class.
        name: Class name.
        teacher_id: UUID of the assigned teacher.
        teacher_name: Display name of the assigned teacher.
        starts_on: Start date.
        finishes_on: Finish date.
        member_count: Number of currently-active members.
        is_upcoming: True when ``starts_on`` is in the future.
        is_running: True when ``starts_on <= today <= finishes_on``.
    """

    class_id: str
    name: str
    teacher_id: str
    teacher_name: str
    starts_on: date
    finishes_on: date
    member_count: int
    is_upcoming: bool
    is_running: bool


@dataclass(frozen=True)
class ClassDetail:
    """Full class details for class pages and registration modals."""

    class_id: str
    name: str
    description: str | None
    teacher_id: str
    teacher_name: str
    teacher_email: str
    teacher_affiliation: str | None
    starts_on: date
    finishes_on: date
    allow_self_registration: bool
    member_count: int
    is_upcoming: bool
    is_running: bool


@dataclass(frozen=True)
class UserClassRow(ClassDetail):
    """Class row for the current user's classes tab."""

    status: Literal["registered", "pending", "denied"]
    request_id: str | None
    requested_at: datetime | None
    denial_reason: str | None
    has_open_problem_set: bool


@dataclass(frozen=True)
class ManagedClassRow(ClassDetail):
    """Class row for teacher/admin management."""


@dataclass(frozen=True)
class ClassMemberManagementRow:
    """Row for the class membership management page."""

    row_id: str
    user_id: str
    request_id: str | None
    name: str
    email: str
    status: Literal["active", "pending"]
    registered_at: date | datetime


@dataclass(frozen=True)
class TeacherAutocompleteRow:
    """Teacher row returned by the admin teacher autocomplete endpoint."""

    user_id: str
    label: str


@dataclass(frozen=True)
class StudentAutocompleteRow:
    """Student row returned by the class member autocomplete endpoint."""

    user_id: str
    label: str


ClassSort = Literal["name", "starts_on"]
SortDir = Literal["asc", "desc"]
MemberSort = Literal["name", "registered_at"]


def _active_members_subquery() -> Select[tuple[str, str]]:
    """Return a subquery of ``(class_id, user_id)`` currently-active memberships.

    The current status for a (class, user) pair is the row with the latest
    ``event_date``; this resolves it portably without window functions.
    """
    latest = (
        select(
            arena_class_memberships.c.class_id.label("class_id"),
            arena_class_memberships.c.user_id.label("user_id"),
            func.max(arena_class_memberships.c.event_date).label("max_date"),
        )
        .group_by(arena_class_memberships.c.class_id, arena_class_memberships.c.user_id)
        .subquery()
    )
    return (
        select(arena_class_memberships.c.class_id, arena_class_memberships.c.user_id)
        .select_from(
            arena_class_memberships.join(
                latest,
                (arena_class_memberships.c.class_id == latest.c.class_id)
                & (arena_class_memberships.c.user_id == latest.c.user_id)
                & (arena_class_memberships.c.event_date == latest.c.max_date),
            )
        )
        .where(arena_class_memberships.c.status == ArenaClassMembershipStatus.ACTIVE.value)
    )


def _assert_teacher_or_admin(
    *,
    teacher_id: str,
    actor_id: str,
    actor_role: ArenaRole,
) -> None:
    """Raise ``ArenaClassPermissionError`` unless the actor owns the class or is admin.

    Args:
        teacher_id: The class's assigned teacher id.
        actor_id: The acting user's id.
        actor_role: The acting user's role.

    Raises:
        ArenaClassPermissionError: When the actor is neither the teacher nor an admin.
    """
    if actor_role == ArenaRole.ARENA_ADMIN:
        return
    if actor_id == teacher_id:
        return
    raise ArenaClassPermissionError("Only the assigned teacher or an admin may do this.")


async def create_class(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    name: str,
    starts_on: date,
    finishes_on: date,
    description: str | None = None,
    teacher_id: str | None = None,
    allow_self_registration: bool = False,
) -> ArenaClass:
    """Create a class.

    An ``ARENA_JUDGE`` actor becomes the assigned teacher. An ``ARENA_ADMIN``
    actor must pass ``teacher_id`` referencing an ``ARENA_JUDGE`` user, who
    becomes the owner. Any other role is rejected.

    Args:
        session: Active async database session (caller commits).
        actor_id: Id of the user creating the class.
        actor_role: Role of the creating user.
        name: Class name (non-empty after stripping).
        starts_on: Start date.
        finishes_on: Finish date (must be >= ``starts_on``).
        description: Optional description.
        teacher_id: Required for ARENA_ADMIN; ignored for ARENA_JUDGE.
        allow_self_registration: When True, the class accepts self-service
            registration requests (default False).

    Returns:
        The created ``ArenaClass``.

    Raises:
        ArenaClassPermissionError: When the actor role may not create classes.
        ArenaClassValidationError: When inputs or the designated teacher are invalid.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise ArenaClassValidationError("Class name must not be empty.")
    if finishes_on < starts_on:
        raise ArenaClassValidationError("finishes_on must not be before starts_on.")

    if actor_role == ArenaRole.ARENA_JUDGE:
        owner_id = actor_id
    elif actor_role == ArenaRole.ARENA_ADMIN:
        if not teacher_id:
            raise ArenaClassValidationError("An admin must designate a teacher_id.")
        teacher = await session.get(ArenaUser, teacher_id)
        if teacher is None:
            raise ArenaClassValidationError("Designated teacher does not exist.")
        if teacher.role != ArenaRole.ARENA_JUDGE:
            raise ArenaClassValidationError("Designated teacher must be an ARENA_JUDGE user.")
        owner_id = teacher_id
    else:
        raise ArenaClassPermissionError("Only ARENA_JUDGE or ARENA_ADMIN may create classes.")

    arena_class = ArenaClass(
        name=clean_name,
        description=(description.strip() if description and description.strip() else None),
        teacher_id=owner_id,
        starts_on=starts_on,
        finishes_on=finishes_on,
        allow_self_registration=allow_self_registration,
    )
    session.add(arena_class)
    await session.flush()
    return arena_class


async def update_class(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    today: date,
    name: str,
    starts_on: date,
    finishes_on: date,
    description: str | None = None,
    teacher_id: str | None = None,
    allow_self_registration: bool = False,
) -> ArenaClass:
    """Update editable class fields with role and date validation.

    Args:
        session: Active async database session (caller commits).
        actor_id: Acting user's id.
        actor_role: Acting user's role.
        class_id: UUID of the class to update.
        today: Reference date for past-date validation.
        name: New class name.
        starts_on: New start date.
        finishes_on: New finish date.
        description: Optional description.
        teacher_id: New teacher (admin only; ignored for judges).
        allow_self_registration: New self-registration flag.

    Returns:
        The updated ``ArenaClass``.

    Raises:
        ArenaClassNotFoundError: When the class does not exist.
        ArenaClassPermissionError: When the actor may not edit this class.
        ArenaClassValidationError: When inputs or the designated teacher are invalid.
    """
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaClassNotFoundError("Class does not exist.")
    _assert_teacher_or_admin(
        teacher_id=arena_class.teacher_id,
        actor_id=actor_id,
        actor_role=actor_role,
    )

    clean_name = (name or "").strip()
    if not clean_name:
        raise ArenaClassValidationError("Class name must not be empty.")
    if starts_on != arena_class.starts_on and starts_on < today:
        raise ArenaClassValidationError("Start date cannot be in the past.")
    if finishes_on != arena_class.finishes_on and finishes_on < today:
        raise ArenaClassValidationError("End date cannot be in the past.")
    if finishes_on < starts_on:
        raise ArenaClassValidationError("finishes_on must not be before starts_on.")
    if starts_on != arena_class.starts_on and arena_class.starts_on <= today:
        raise ArenaClassValidationError("Start date cannot be changed after the class has started.")
    if finishes_on != arena_class.finishes_on and arena_class.finishes_on < today:
        raise ArenaClassValidationError("End date cannot be changed after the class has finished.")

    if actor_role == ArenaRole.ARENA_ADMIN and teacher_id and teacher_id != arena_class.teacher_id:
        teacher = await session.get(ArenaUser, teacher_id)
        if teacher is None:
            raise ArenaClassValidationError("Designated teacher does not exist.")
        if teacher.role != ArenaRole.ARENA_JUDGE:
            raise ArenaClassValidationError("Designated teacher must be an ARENA_JUDGE user.")
        arena_class.teacher_id = teacher_id

    arena_class.name = clean_name
    arena_class.description = description.strip() if description and description.strip() else None
    arena_class.starts_on = starts_on
    arena_class.finishes_on = finishes_on
    arena_class.allow_self_registration = allow_self_registration
    await session.flush()
    return arena_class
