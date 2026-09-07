#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Clarification DTOs and view helpers."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from shared.enumerations import RoleEnum
from shared.services.lock_service import LockBatchResult
from web.models.clarification import Clarification
from web.models.users import UberAdmin, User


@dataclass
class ClarificationView:
    """Role-scoped projection of a clarification."""

    id: str
    problem_id: str | None
    team_id: str
    question: str
    answer: str | None
    is_contest_public: bool
    is_announcement: bool
    unread: bool
    answered_at: datetime.datetime | None
    answer_read_at: datetime.datetime | None
    acquired_at: datetime.datetime | None
    service_started_at: datetime.datetime | None
    hidden: bool
    hidden_at: datetime.datetime | None
    created_at: datetime.datetime
    created_timestamp_seconds: int
    judge_id: str | None
    acquired_by_me: bool


def is_unread_for(
    clari: Clarification,
    *,
    actor: User | UberAdmin,
    read_announcement_ids: frozenset[str],
) -> bool:
    """Decide whether *actor* has an unread notification on this clarification.

    This is the single definition the dashboard counter, the list highlight, and the
    acknowledgement endpoint all answer to. Only a team is ever notified: about an answer
    to a question it asked, or about an announcement it has not acknowledged.

    Args:
        clari: Clarification being projected.
        actor: Viewer the projection is for.
        read_announcement_ids: Announcement ids this actor has already read.

    Returns:
        True when the actor should see this row highlighted as new.
    """
    if isinstance(actor, UberAdmin) or actor.role != RoleEnum.TEAM:
        return False
    if clari.hidden:
        return False
    if clari.is_announcement:
        # The Python-side mirror of `announcement_visible_to_teams()`: a team is never
        # notified about a row its own list would not show it.
        return clari.is_contest_public and clari.id not in read_announcement_ids
    return clari.team_id == actor.id and clari.answered_at is not None and clari.answer_read_at is None


def to_view(
    clari: Clarification,
    *,
    show_judge: bool,
    actor_id: str | None,
    unread: bool,
) -> ClarificationView:
    """Project one clarification to its role-scoped DTO.

    ``acquired_at`` reflects a *live* lock only, and is left ``None`` here for
    ``merge_clarification_views`` to fill in -- it doubles as the "someone is
    working on this right now" signal the templates gate buttons on.
    ``service_started_at`` is the persisted acquisition instant and carries no
    such meaning: it is set whenever the row was last acquired, whether or not
    that lock still exists.
    """
    return ClarificationView(
        id=clari.id,
        problem_id=clari.problem_id,
        team_id=clari.team_id,
        question=clari.question,
        answer=clari.answer,
        is_contest_public=clari.is_contest_public,
        is_announcement=clari.is_announcement,
        unread=unread,
        answered_at=clari.answered_at,
        answer_read_at=clari.answer_read_at,
        acquired_at=None,
        service_started_at=clari.acquired_at,
        hidden=clari.hidden,
        hidden_at=clari.hidden_at,
        created_at=clari.created_at,
        created_timestamp_seconds=clari.created_timestamp_seconds,
        judge_id=clari.judge_id if show_judge else None,
        acquired_by_me=clari.judge_id is not None and clari.judge_id == actor_id,
    )


def merge_clarification_views(
    clarifications: list[Clarification],
    *,
    actor: User | UberAdmin,
    show_judge: bool,
    lock_batch: LockBatchResult,
    read_announcement_ids: frozenset[str] = frozenset(),
) -> list[ClarificationView]:
    """Merge database clarifications with lock and read state for UI consumption."""
    actor_id = None if isinstance(actor, UberAdmin) else actor.id
    views: list[ClarificationView] = []
    for clari in clarifications:
        view = to_view(
            clari,
            show_judge=show_judge,
            actor_id=actor_id,
            unread=is_unread_for(clari, actor=actor, read_announcement_ids=read_announcement_ids),
        )
        if clari.answered_at is not None:
            views.append(view)
            continue
        lock = lock_batch.locks_by_resource_id.get(clari.id)
        if lock is None:
            views.append(view)
            continue
        view.acquired_at = lock.acquired_at
        view.judge_id = lock.holder_id if show_judge else None
        view.acquired_by_me = actor_id is not None and lock.holder_id == actor_id
        views.append(view)
    return views
