#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Lifecycle operations for contest clarifications."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.lock_service import (
    LockClient,
    acquire_lock,
    force_release_lock,
    get_lock,
    release_lock,
)
from shared.timing import compute_timestamp_seconds
from web.models._base import _utcnow
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User

from .errors import (
    ClarificationAlreadyAcquiredError,
    ClarificationAlreadyAnsweredError,
    ClarificationHiddenError,
    ClarificationLockUnavailableError,
    ClarificationNotAcquiredByActorError,
    ContestNotRunningError,
    ForbiddenClarificationActionError,
)
from .permissions import (
    can_answer_clarifications,
    can_create_announcement,
    can_force_release_clarifications,
    can_request_clarification,
)


async def _require_contest_problem(session: AsyncSession, contest: Contest, problem_id: str | None) -> None:
    """Validate that *problem_id*, when given, belongs to *contest*.

    Args:
        session: Active database session.
        contest: Contest the problem must belong to.
        problem_id: Problem identifier, or ``None`` for a general clarification.

    Raises:
        ValueError: If the problem does not belong to the contest.
    """
    if problem_id is None:
        return
    result = await session.execute(select(Problem).where(Problem.id == problem_id, Problem.contest_id == contest.id))
    if result.scalar_one_or_none() is None:
        raise ValueError(f"Problem '{problem_id}' not found in contest '{contest.id}'.")


async def _resolve_clarification_contest(session: AsyncSession, clarification: Clarification) -> Contest | None:
    """Resolve the contest a clarification belongs to.

    General clarifications carry no problem, so the contest is resolved through the
    author user, which is always contest-scoped.

    Args:
        session: Active database session.
        clarification: Clarification to resolve.

    Returns:
        The owning contest, or ``None`` when it cannot be resolved.
    """
    if clarification.problem_id is not None:
        problem = await session.get(Problem, clarification.problem_id)
        if problem is not None:
            return await session.get(Contest, problem.contest_id)
    author = await session.get(User, clarification.team_id)
    if author is None or author.contest_id is None:
        return None
    return await session.get(Contest, author.contest_id)


async def create_clarification(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    *,
    problem_id: str | None,
    question: str,
) -> Clarification:
    """Create a new clarification on behalf of a team.

    Args:
        session: Active database session.
        contest: Contest the clarification belongs to.
        actor: Team submitting the question.
        problem_id: Problem the question is about, or ``None`` for a general
            contest-wide clarification.
        question: Question text.

    Returns:
        The created clarification.

    Raises:
        ContestNotRunningError: If the contest is not running.
        ForbiddenClarificationActionError: If the actor is not a team.
        ValueError: If *problem_id* does not belong to the contest.
    """
    if actor.role != RoleEnum.TEAM:
        raise ForbiddenClarificationActionError("Only team members may submit clarifications.")
    if not can_request_clarification(actor, contest):
        raise ContestNotRunningError("Clarifications can only be requested while the contest is running.")

    await _require_contest_problem(session, contest, problem_id)

    now = _utcnow()
    clarification = Clarification(
        team_id=actor.id,
        problem_id=problem_id,
        question=question.strip(),
        created_at=now,
        created_timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(clarification)
    await session.flush()
    return clarification


async def acquire_clarification(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    clarification: Clarification,
    lock_client: LockClient,
) -> Clarification:
    """Acquire a clarification lock so a judge or admin may answer it."""
    if not contest.is_running:
        raise ContestNotRunningError("Clarifications can only be acquired while the contest is running.")
    if not can_answer_clarifications(actor):
        raise ForbiddenClarificationActionError("Only judges and admins may acquire clarifications.")
    if clarification.answered_at is not None:
        raise ClarificationAlreadyAnsweredError("This clarification has already been answered.")
    if clarification.hidden:
        raise ClarificationHiddenError("This clarification is hidden and cannot be acquired.")

    timeout_minutes = contest.clarifications_timeout_minutes
    ttl_seconds = contest.remaining_time_seconds if timeout_minutes == 0 else timeout_minutes * 60
    acquired = await acquire_lock(
        lock_client,
        kind="clarification",
        contest_id=contest.id,
        resource_id=clarification.id,
        holder_id=actor.id,
        holder_role=actor.role.value,
        ttl_seconds=ttl_seconds,
    )
    if acquired is None:
        raise ClarificationLockUnavailableError("Clarification locks are currently unavailable.")
    if not acquired:
        raise ClarificationAlreadyAcquiredError("This clarification is already acquired by another judge.")
    return clarification


async def release_clarification(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    clarification: Clarification,
    lock_client: LockClient,
) -> Clarification:
    """Release an answerer's lock on a clarification without answering it."""
    can_force = can_force_release_clarifications(actor)
    if not can_force and not can_answer_clarifications(actor):
        raise ForbiddenClarificationActionError("Only judges and admins may release clarification locks.")

    lock = await get_lock(lock_client, kind="clarification", contest_id=contest.id, resource_id=clarification.id)
    if lock is None:
        if can_force:
            return clarification
        raise ClarificationNotAcquiredByActorError("You do not hold the lock on this clarification.")

    if can_force:
        await force_release_lock(
            lock_client,
            kind="clarification",
            contest_id=contest.id,
            resource_id=clarification.id,
        )
        return clarification

    assert isinstance(actor, User)
    if lock.holder_id != actor.id:
        raise ClarificationNotAcquiredByActorError("You do not hold the lock on this clarification.")
    await release_lock(
        lock_client,
        kind="clarification",
        contest_id=contest.id,
        resource_id=clarification.id,
        holder_id=actor.id,
    )
    return clarification


async def answer_clarification(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    clarification: Clarification,
    lock_client: LockClient,
    *,
    answer: str,
    is_contest_public: bool,
) -> Clarification:
    """Submit an answer to an acquired clarification."""
    if not contest.is_running:
        raise ContestNotRunningError("Clarifications can only be answered while the contest is running.")
    if not can_answer_clarifications(actor):
        raise ForbiddenClarificationActionError("Only judges and admins may answer clarifications.")
    if clarification.hidden:
        raise ClarificationHiddenError("Hidden clarifications cannot be answered.")
    if clarification.answered_at is not None:
        raise ClarificationAlreadyAnsweredError("This clarification has already been answered.")

    lock = await get_lock(lock_client, kind="clarification", contest_id=contest.id, resource_id=clarification.id)
    if lock_client is not None and lock is not None and lock.holder_id != actor.id:
        raise ClarificationNotAcquiredByActorError("You must acquire this clarification before answering it.")
    if lock_client is not None and lock is None and getattr(lock_client, "is_available", True):
        raise ClarificationNotAcquiredByActorError("You must acquire this clarification before answering it.")

    now = _utcnow()
    clarification.judge_id = actor.id
    clarification.answer = answer.strip()
    clarification.answered_at = now
    clarification.answer_read_at = None
    clarification.answered_timestamp_seconds = compute_timestamp_seconds(contest.start_time, now)
    clarification.is_contest_public = is_contest_public
    await session.flush()
    if lock is not None:
        await release_lock(
            lock_client,
            kind="clarification",
            contest_id=contest.id,
            resource_id=clarification.id,
            holder_id=actor.id,
        )
    return clarification


async def create_announcement(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    *,
    problem_id: str | None,
    announcement: str,
) -> Clarification:
    """Create a public announcement as a clarification initiated by a judge or admin.

    The signature accepts `User | UberAdmin` to match `can_create_announcement`
    and the rest of the module; an uberadmin caller is refused by the role
    check below, before any `users` foreign key is touched.

    Args:
        session: Active database session.
        contest: Contest the announcement belongs to.
        actor: Judge or admin publishing the announcement.
        problem_id: Problem the announcement is about, or ``None`` for a general
            contest-wide announcement.
        announcement: Announcement text.

    Returns:
        The created, already-answered clarification.

    Raises:
        ForbiddenClarificationActionError: If the actor is neither judge nor admin.
        ContestNotRunningError: If the contest is not running and the actor is
            neither a contest admin nor the contest's chief judge.
        ValueError: If *problem_id* does not belong to the contest.
    """
    if actor.role not in (RoleEnum.ADMIN, RoleEnum.JUDGE):
        raise ForbiddenClarificationActionError("Only admins and judges may create announcements.")
    if not can_create_announcement(actor, contest):
        raise ContestNotRunningError(
            "Announcements can only be created while the contest is running, "
            "unless published by a contest admin or the chief judge."
        )

    await _require_contest_problem(session, contest, problem_id)

    now = _utcnow()
    clarification = Clarification(
        team_id=actor.id,
        judge_id=actor.id,
        problem_id=problem_id,
        question="Announcement",
        answer=announcement.strip(),
        is_contest_public=True,
        is_announcement=True,
        created_at=now,
        created_timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
        answered_at=now,
        answered_timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(clarification)
    await session.flush()
    return clarification


async def toggle_hidden_clarification(
    session: AsyncSession,
    actor: User | UberAdmin,
    clarification: Clarification,
) -> Clarification:
    """Toggle the hidden status of a clarification."""
    is_uber_or_admin = isinstance(actor, UberAdmin) or (
        not isinstance(actor, UberAdmin) and actor.role == RoleEnum.ADMIN
    )
    is_judge = not isinstance(actor, UberAdmin) and actor.role == RoleEnum.JUDGE
    if not is_uber_or_admin and not is_judge:
        raise ForbiddenClarificationActionError(
            "Only judges, admins, and UberAdmins may hide or unhide clarifications."
        )

    contest = await _resolve_clarification_contest(session, clarification)

    if not clarification.hidden:
        now = _utcnow()
        clarification.hidden = True
        clarification.hidden_at = now
        clarification.hidden_timestamp_seconds = (
            compute_timestamp_seconds(contest.start_time, now) if contest is not None else None
        )
        if is_judge:
            clarification.hidden_by_judge_id = actor.id
            clarification.hidden_by_admin_id = None
        else:
            clarification.hidden_by_admin_id = actor.id
            clarification.hidden_by_judge_id = None
    else:
        clarification.hidden = False
        clarification.hidden_at = None
        clarification.hidden_timestamp_seconds = None
        clarification.hidden_by_judge_id = None
        clarification.hidden_by_admin_id = None

    await session.flush()
    return clarification
