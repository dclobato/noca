#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Clarification DTOs and view helpers."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from shared.services.lock_service import LockBatchResult
from web.models.clarification import Clarification
from web.models.users import UberAdmin, User


@dataclass
class ClarificationView:
    """Role-scoped projection of a clarification."""

    id: str
    problem_id: str
    team_id: str
    question: str
    answer: str | None
    is_contest_public: bool
    answered_at: datetime.datetime | None
    acquired_at: datetime.datetime | None
    hidden: bool
    hidden_at: datetime.datetime | None
    created_at: datetime.datetime
    created_timestamp_seconds: int
    judge_id: str | None
    acquired_by_me: bool


def to_view(clari: Clarification, *, show_judge: bool, actor_id: str | None) -> ClarificationView:
    """Project one clarification to its role-scoped DTO."""
    return ClarificationView(
        id=clari.id,
        problem_id=clari.problem_id,
        team_id=clari.team_id,
        question=clari.question,
        answer=clari.answer,
        is_contest_public=clari.is_contest_public,
        answered_at=clari.answered_at,
        acquired_at=None,
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
) -> list[ClarificationView]:
    """Merge database clarifications with lock state for UI consumption."""
    actor_id = None if isinstance(actor, UberAdmin) else actor.id
    views: list[ClarificationView] = []
    for clari in clarifications:
        view = to_view(clari, show_judge=show_judge, actor_id=actor_id)
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
