#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM models for Arena classes, memberships, and registration requests.

These models map onto the Core tables defined in
``shared.db_schema.arena.arena_classes``:

  - ArenaClass: a class owned by an assigned teacher (ARENA_JUDGE user).
  - ArenaClassMembership: dated membership-status history per (class, user).
  - ArenaClassRegistrationRequest: self-service registration request lifecycle.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena import arena_class_memberships as arena_class_memberships_table
from shared.db_schema.arena import (
    arena_class_registration_requests as arena_class_registration_requests_table,
)
from shared.db_schema.arena import arena_classes as arena_classes_table

if TYPE_CHECKING:
    from arena.models.arena_problem_sets import ArenaProblemSet
    from arena.models.arena_users import ArenaUser


class ArenaClass(ArenaBase):
    """A class owned by an assigned teacher.

    Maps onto the ``arena_classes`` Core table.

    Attributes:
        id: UUID string primary key.
        name: Display name of the class.
        description: Optional free-text description.
        teacher_id: FK to arena_users; the assigned teacher (ARENA_JUDGE).
        starts_on: Date the class starts (date only).
        finishes_on: Date the class finishes (date only).
        allow_self_registration: When True, users may discover and request to join.
        created_at: Record creation timestamp.
        updated_at: Record last-update timestamp.
        teacher: The owning ArenaUser (assigned teacher).
        memberships: Dated membership-status history rows.
        registration_requests: Self-service registration requests.
        problem_sets: Problem sets owned by this class (placeholder concept).
    """

    __table__ = arena_classes_table

    id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str | None]
    teacher_id: Mapped[str]
    starts_on: Mapped[date]
    finishes_on: Mapped[date]
    allow_self_registration: Mapped[bool]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    teacher: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        foreign_keys=[arena_classes_table.c.teacher_id],
    )
    memberships: Mapped[list[ArenaClassMembership]] = relationship(
        "ArenaClassMembership",
        back_populates="arena_class",
        cascade="all, delete-orphan",
    )
    registration_requests: Mapped[list[ArenaClassRegistrationRequest]] = relationship(
        "ArenaClassRegistrationRequest",
        back_populates="arena_class",
        cascade="all, delete-orphan",
    )
    problem_sets: Mapped[list[ArenaProblemSet]] = relationship(
        "ArenaProblemSet",
        back_populates="arena_class",
        cascade="all, delete-orphan",
    )


class ArenaClassMembership(ArenaBase):
    """One dated membership-status row for a (class, user) pair.

    Maps onto the ``arena_class_memberships`` Core table. The composite primary
    key ``(class_id, user_id, event_date)`` means a same-day status flip
    overwrites that day's row; the current membership is the row with the
    latest ``event_date``.

    Attributes:
        class_id: Composite PK + FK to arena_classes (CASCADE).
        user_id: Composite PK + FK to arena_users (CASCADE).
        event_date: Composite PK; date the status took effect (date only).
        status: Membership status string (ACTIVE or REMOVED).
        arena_class: Back-reference to the owning ArenaClass.
        user: The ArenaUser this membership refers to.
    """

    __table__ = arena_class_memberships_table

    class_id: Mapped[str]
    user_id: Mapped[str]
    event_date: Mapped[date]
    status: Mapped[str]

    arena_class: Mapped[ArenaClass] = relationship(
        "ArenaClass",
        back_populates="memberships",
        foreign_keys=[arena_class_memberships_table.c.class_id],
    )
    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        foreign_keys=[arena_class_memberships_table.c.user_id],
    )


class ArenaClassRegistrationRequest(ArenaBase):
    """A self-service request by a user to join a class.

    Maps onto the ``arena_class_registration_requests`` Core table.

    Attributes:
        id: UUID string primary key.
        class_id: FK to arena_classes (CASCADE).
        user_id: FK to arena_users (CASCADE); the requesting user.
        status: Request lifecycle string (PENDING, APPROVED, DENIED).
        requested_at: When the user submitted the request.
        decided_at: When a teacher/admin decided the request.
        decided_by_id: FK to arena_users; who decided the request.
        denial_reason: Optional reason given when denying the request.
        arena_class: Back-reference to the owning ArenaClass.
        user: The requesting ArenaUser.
    """

    __table__ = arena_class_registration_requests_table

    id: Mapped[str]
    class_id: Mapped[str]
    user_id: Mapped[str]
    status: Mapped[str]
    requested_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
    decided_by_id: Mapped[str | None]
    denial_reason: Mapped[str | None]

    arena_class: Mapped[ArenaClass] = relationship(
        "ArenaClass",
        back_populates="registration_requests",
        foreign_keys=[arena_class_registration_requests_table.c.class_id],
    )
    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        foreign_keys=[arena_class_registration_requests_table.c.user_id],
    )
