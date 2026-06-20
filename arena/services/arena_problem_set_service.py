#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service for Arena problem sets.

A *problem set* belongs to exactly one class and is created only by the assigned
teacher (an ``ARENA_ADMIN`` may act on any class). It has an optional
``starts_on``/``deadline`` window: it "accepts submissions" only while
``starts_on <= now <= deadline``. Problems belong to a set through the
``arena_problem_set_problems`` junction.

A submission becomes visible to the set's teacher only when the user explicitly
ties it to the set at submit time (``arena_submissions.problem_set_id``). Removing
a problem from a set, or deleting the set, resets the related submissions to
private (``problem_set_id = NULL``).

All functions take an explicit ``AsyncSession`` and never commit; the caller owns
the transaction boundary (``session.flush()`` is used internally).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_classes import ArenaClass
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.services.arena_class_service import _active_members_subquery
from shared.db_schema.arena import (
    arena_problem_set_problems,
    arena_problems,
    arena_submission_judgments,
    arena_submissions,
)
from shared.enumerations import ArenaRole


class ArenaProblemSetServiceError(Exception):
    """Base error for Arena problem-set services."""


class ArenaProblemSetNotFoundError(ArenaProblemSetServiceError):
    """Raised when a referenced problem set does not exist."""


class ArenaProblemSetPermissionError(ArenaProblemSetServiceError):
    """Raised when the actor is not allowed to perform the operation."""


class ArenaProblemSetValidationError(ArenaProblemSetServiceError):
    """Raised when input validation fails."""


@dataclass(frozen=True)
class ProblemSetRow:
    """A problem set with scheduling and counts."""

    set_id: str
    class_id: str
    name: str
    description: str | None
    starts_on: datetime | None
    deadline: datetime | None
    is_accepting: bool
    problem_count: int


@dataclass(frozen=True)
class ProblemRow:
    """A problem belonging to a set."""

    problem_id: str
    arena_number: int
    title: str


@dataclass(frozen=True)
class AcceptingSetInfo:
    """The single accepting set that drives the problem-detail banner/checkbox."""

    set_id: str
    name: str
    class_id: str
    class_name: str
    deadline: datetime | None
    has_others: bool = False


async def _load_set_and_class(session: AsyncSession, set_id: str) -> tuple[ArenaProblemSet, ArenaClass]:
    """Load a problem set and its owning class, or raise NotFound."""
    problem_set = await session.get(ArenaProblemSet, set_id)
    if problem_set is None:
        raise ArenaProblemSetNotFoundError("Problem set does not exist.")
    arena_class = await session.get(ArenaClass, problem_set.class_id)
    if arena_class is None:  # pragma: no cover - FK guarantees presence
        raise ArenaProblemSetNotFoundError("Problem set does not exist.")
    return problem_set, arena_class


def _assert_teacher(arena_class: ArenaClass, *, actor_id: str, actor_role: ArenaRole) -> None:
    """Raise unless the actor owns the class or is an admin."""
    if actor_role == ArenaRole.ARENA_ADMIN or actor_id == arena_class.teacher_id:
        return
    raise ArenaProblemSetPermissionError("Only the assigned teacher or an admin may do this.")


async def _is_active_member(session: AsyncSession, *, class_id: str, user_id: str) -> bool:
    """Return True when the user is a currently-active member of the class."""
    active = _active_members_subquery().subquery()
    found = await session.scalar(
        select(active.c.user_id).where(active.c.class_id == class_id, active.c.user_id == user_id)
    )
    return found is not None


async def _assert_can_view(
    session: AsyncSession, arena_class: ArenaClass, *, actor_id: str, actor_role: ArenaRole
) -> None:
    """Allow the teacher, an admin, or an active member of the class to view."""
    if actor_role == ArenaRole.ARENA_ADMIN or actor_id == arena_class.teacher_id:
        return
    if await _is_active_member(session, class_id=arena_class.id, user_id=actor_id):
        return
    raise ArenaProblemSetPermissionError("You are not allowed to view this problem set.")


def _as_utc(value: datetime) -> datetime:
    """Interpret a naive datetime as UTC (a ``timezone=True`` column is UTC)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _class_start_bound(d: date) -> datetime:
    """Return the UTC datetime for the opening boundary of a class (00:00:00 UTC of d)."""
    return datetime.combine(d, time.min).replace(tzinfo=UTC)


def _class_end_bound(d: date) -> datetime:
    """Return the UTC datetime for the closing boundary of a class (23:59:59 UTC of d)."""
    return datetime.combine(d, time(23, 59, 59)).replace(tzinfo=UTC)


def _assert_within_class_period(
    arena_class: ArenaClass,
    starts_on: datetime | None,
    deadline: datetime | None,
) -> None:
    """Raise ArenaProblemSetValidationError when dates fall outside the class period."""
    lower = _class_start_bound(arena_class.starts_on)
    upper = _class_end_bound(arena_class.finishes_on)
    if starts_on is not None and _as_utc(starts_on) < lower:
        raise ArenaProblemSetValidationError(
            f"Problem set start cannot be before the class start ({arena_class.starts_on.isoformat()})."
        )
    if deadline is not None and _as_utc(deadline) > upper:
        raise ArenaProblemSetValidationError(
            f"Problem set deadline cannot exceed the class end ({arena_class.finishes_on.isoformat()} 23:59:59 UTC)."
        )


def _is_accepting(problem_set: ArenaProblemSet, now: datetime) -> bool:
    """Return True when the set window contains now (NULL date = no restriction)."""
    if problem_set.starts_on is not None and _as_utc(problem_set.starts_on) > _as_utc(now):
        return False
    return problem_set.deadline is None or _as_utc(problem_set.deadline) >= _as_utc(now)


async def _set_problem_ids(session: AsyncSession, set_id: str) -> set[str]:
    """Return the problem ids currently in the set."""
    rows = await session.execute(
        select(arena_problem_set_problems.c.problem_id).where(arena_problem_set_problems.c.problem_set_id == set_id)
    )
    return {row[0] for row in rows.all()}


async def _resolve_problem_refs(session: AsyncSession, refs: Sequence[str | int]) -> list[str]:
    """Resolve problem references (UUID id or arena_number) to problem ids.

    Raises:
        ArenaProblemSetValidationError: When a reference matches no problem.
    """
    if not refs:
        raise ArenaProblemSetValidationError("No problems were given.")
    resolved: list[str] = []
    for ref in refs:
        text = str(ref).strip()
        if not text:
            raise ArenaProblemSetValidationError("Empty problem reference.")
        if text.isdigit():
            problem_id = await session.scalar(
                select(arena_problems.c.id).where(arena_problems.c.arena_number == int(text))
            )
        else:
            problem_id = await session.scalar(select(arena_problems.c.id).where(arena_problems.c.id == text))
        if problem_id is None:
            raise ArenaProblemSetValidationError(f"Problem '{text}' was not found.")
        resolved.append(problem_id)
    return resolved


async def create_problem_set(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    name: str,
    description: str | None = None,
) -> ArenaProblemSet:
    """Create a problem set in a class (assigned teacher or admin only)."""
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaProblemSetNotFoundError("Class does not exist.")
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    clean_name = (name or "").strip()
    clean_description = (description or "").strip() or None
    if not clean_name:
        raise ArenaProblemSetValidationError("Problem set name is required.")
    problem_set = ArenaProblemSet(class_id=class_id, name=clean_name, description=clean_description)
    session.add(problem_set)
    await session.flush()
    return problem_set


async def delete_problem_set(session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str) -> None:
    """Delete a problem set; related submissions become private (assigned teacher/admin)."""
    problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    await session.execute(
        update(arena_submissions).where(arena_submissions.c.problem_set_id == set_id).values(problem_set_id=None)
    )
    await session.delete(problem_set)
    await session.flush()


async def set_problem_set_schedule(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    starts_on: datetime | None,
    deadline: datetime | None,
    now: datetime,
) -> ArenaProblemSet:
    """Set the startline/deadline window of a set (assigned teacher/admin).

    Both dates may be omitted together to create a perpetual (always-open) set.
    When provided, each date must not be in the past, and deadline must be
    strictly after starts_on.
    """
    problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    now_utc = _as_utc(now)
    if starts_on is not None and _as_utc(starts_on) < now_utc:
        raise ArenaProblemSetValidationError("Start date must not be in the past.")
    if deadline is not None and _as_utc(deadline) < now_utc:
        raise ArenaProblemSetValidationError("Deadline must not be in the past.")
    if starts_on is not None and deadline is not None and _as_utc(deadline) <= _as_utc(starts_on):
        raise ArenaProblemSetValidationError("Deadline must be after the start date.")
    _assert_within_class_period(arena_class, starts_on, deadline)
    problem_set.starts_on = starts_on
    problem_set.deadline = deadline
    await session.flush()
    return problem_set


def _truncate_to_minutes(dt: datetime) -> datetime:
    """Strip seconds and microseconds for minute-precision comparison."""
    return dt.replace(second=0, microsecond=0)


async def update_problem_set_schedule(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    starts_on: datetime | None,
    deadline: datetime | None,
    now: datetime,
) -> ArenaProblemSet:
    """Update the schedule window, allowing already-past dates to remain unchanged.

    Validation rules:
    - starts_on: if changed from the stored value (minute precision), must not be in the past.
    - deadline: if changed from the stored value and starts_on is set, must be after starts_on;
      if changed and starts_on is not set, must be in the future.
    - When both are supplied: deadline must be strictly after starts_on.
    """
    problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    now_utc = _as_utc(now)
    new_starts = _as_utc(starts_on) if starts_on is not None else None
    new_deadline = _as_utc(deadline) if deadline is not None else None
    current_starts = _as_utc(problem_set.starts_on) if problem_set.starts_on is not None else None
    current_deadline = _as_utc(problem_set.deadline) if problem_set.deadline is not None else None

    if new_starts is not None:
        starts_changed = current_starts is None or (
            _truncate_to_minutes(new_starts) != _truncate_to_minutes(current_starts)
        )
        if starts_changed and new_starts < now_utc:
            raise ArenaProblemSetValidationError("Start date must not be in the past.")

    if new_deadline is not None:
        deadline_changed = current_deadline is None or (
            _truncate_to_minutes(new_deadline) != _truncate_to_minutes(current_deadline)
        )
        if deadline_changed:
            if new_starts is not None:
                if new_deadline <= new_starts:
                    raise ArenaProblemSetValidationError("Deadline must be after the start date.")
            else:
                if new_deadline < now_utc:
                    raise ArenaProblemSetValidationError("Deadline must not be in the past.")

    if new_starts is not None and new_deadline is not None and new_deadline <= new_starts:
        raise ArenaProblemSetValidationError("Deadline must be after the start date.")

    _assert_within_class_period(arena_class, starts_on, deadline)
    problem_set.starts_on = starts_on
    problem_set.deadline = deadline
    await session.flush()
    return problem_set


async def update_problem_set_details(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    description: str | None,
    starts_on: datetime | None,
    deadline: datetime | None,
    now: datetime,
) -> ArenaProblemSet:
    """Update teacher-facing notes and the schedule in one transaction."""
    problem_set = await update_problem_set_schedule(
        session,
        actor_id=actor_id,
        actor_role=actor_role,
        set_id=set_id,
        starts_on=starts_on,
        deadline=deadline,
        now=now,
    )
    problem_set.description = (description or "").strip() or None
    await session.flush()
    return problem_set


async def stop_problem_set_now(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    now: datetime,
) -> ArenaProblemSet:
    """Set the deadline to now, immediately stopping submission acceptance."""
    problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    if not _is_accepting(problem_set, now):
        raise ArenaProblemSetValidationError("Problem set is not currently accepting submissions.")
    problem_set.deadline = now
    await session.flush()
    return problem_set


async def _problem_count(session: AsyncSession, set_id: str) -> int:
    """Return the number of problems in a set."""
    return len(await _set_problem_ids(session, set_id))


async def _problem_set_rows(
    session: AsyncSession, class_id: str, now: datetime, *, only_accepting: bool
) -> list[ProblemSetRow]:
    """Build problem-set rows for a class, optionally filtered to accepting sets."""
    sets = (
        (
            await session.execute(
                select(ArenaProblemSet).where(ArenaProblemSet.class_id == class_id).order_by(ArenaProblemSet.name)
            )
        )
        .scalars()
        .all()
    )
    rows: list[ProblemSetRow] = []
    for problem_set in sets:
        accepting = _is_accepting(problem_set, now)
        if only_accepting and not accepting:
            continue
        rows.append(
            ProblemSetRow(
                set_id=problem_set.id,
                class_id=problem_set.class_id,
                name=problem_set.name,
                description=problem_set.description,
                starts_on=problem_set.starts_on,
                deadline=problem_set.deadline,
                is_accepting=accepting,
                problem_count=await _problem_count(session, problem_set.id),
            )
        )
    return rows


async def list_problem_sets_for_class(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, class_id: str, now: datetime
) -> list[ProblemSetRow]:
    """List all problem sets for a class (teacher/admin or active member)."""
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaProblemSetNotFoundError("Class does not exist.")
    await _assert_can_view(session, arena_class, actor_id=actor_id, actor_role=actor_role)
    return await _problem_set_rows(session, class_id, now, only_accepting=False)


async def list_accepting_problem_sets_for_class(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, class_id: str, now: datetime
) -> list[ProblemSetRow]:
    """List problem sets currently accepting submissions (teacher/admin or active member)."""
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaProblemSetNotFoundError("Class does not exist.")
    await _assert_can_view(session, arena_class, actor_id=actor_id, actor_role=actor_role)
    return await _problem_set_rows(session, class_id, now, only_accepting=True)


async def list_problems_in_set(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str
) -> list[ProblemRow]:
    """List the problems belonging to a set (teacher/admin or active member)."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    await _assert_can_view(session, arena_class, actor_id=actor_id, actor_role=actor_role)
    rows = await session.execute(
        select(arena_problems.c.id, arena_problems.c.arena_number, arena_problems.c.title)
        .select_from(
            arena_problem_set_problems.join(
                arena_problems, arena_problem_set_problems.c.problem_id == arena_problems.c.id
            )
        )
        .where(arena_problem_set_problems.c.problem_set_id == set_id)
        .order_by(arena_problems.c.arena_number)
    )
    return [ProblemRow(problem_id=r.id, arena_number=r.arena_number, title=r.title) for r in rows]


async def add_problems_to_set(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    refs: Sequence[str | int],
) -> int:
    """Add problems (by id or arena_number) to a set; idempotent (assigned teacher/admin).

    Returns:
        The number of problems newly added (already-present problems are skipped).
    """
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    problem_ids = await _resolve_problem_refs(session, refs)
    existing = await _set_problem_ids(session, set_id)
    added = 0
    for problem_id in problem_ids:
        if problem_id in existing:
            continue
        await session.execute(arena_problem_set_problems.insert().values(problem_set_id=set_id, problem_id=problem_id))
        existing.add(problem_id)
        added += 1
    await session.flush()
    return added


async def remove_problems_from_set(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    refs: Sequence[str | int],
) -> int:
    """Remove problems from a set; related submissions become private (assigned teacher/admin).

    Returns:
        The number of problems removed from the set.
    """
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    problem_ids = await _resolve_problem_refs(session, refs)
    present = (await _set_problem_ids(session, set_id)) & set(problem_ids)
    await session.execute(
        delete(arena_problem_set_problems).where(
            arena_problem_set_problems.c.problem_set_id == set_id,
            arena_problem_set_problems.c.problem_id.in_(problem_ids),
        )
    )
    await session.execute(
        update(arena_submissions)
        .where(
            arena_submissions.c.problem_set_id == set_id,
            arena_submissions.c.problem_id.in_(problem_ids),
        )
        .values(problem_set_id=None)
    )
    await session.flush()
    return len(present)


async def _set_tied_verdicts(session: AsyncSession, set_id: str) -> dict[tuple[str, str], list[str | None]]:
    """Map ``(user_id, problem_id)`` to the verdicts of their set-tied submissions."""
    rows = await session.execute(
        select(
            arena_submissions.c.user_id,
            arena_submissions.c.problem_id,
            arena_submission_judgments.c.final_verdict,
        )
        .select_from(
            arena_submissions.outerjoin(
                arena_submission_judgments,
                arena_submission_judgments.c.submission_id == arena_submissions.c.id,
            )
        )
        .where(arena_submissions.c.problem_set_id == set_id)
    )
    grouped: dict[tuple[str, str], list[str | None]] = {}
    for r in rows:
        grouped.setdefault((r.user_id, r.problem_id), []).append(r.final_verdict)
    return grouped


async def problem_accepting_set_for_user(
    session: AsyncSession, *, problem_id: str, user_id: str, now: datetime
) -> AcceptingSetInfo | None:
    """Return the most urgent accepting set containing the problem for the user.

    "Most urgent" is the accepting set with the earliest deadline whose class the
    user is currently an active member of. Drives the problem-detail banner/checkbox.
    """
    active = _active_members_subquery().subquery()
    all_rows = (
        await session.execute(
            select(
                ArenaProblemSet.id,
                ArenaProblemSet.name,
                ArenaProblemSet.deadline,
                ArenaClass.id.label("class_id"),
                ArenaClass.name.label("class_name"),
            )
            .select_from(
                arena_problem_set_problems.join(
                    ArenaProblemSet, arena_problem_set_problems.c.problem_set_id == ArenaProblemSet.id
                )
                .join(ArenaClass, ArenaProblemSet.class_id == ArenaClass.id)
                .join(active, active.c.class_id == ArenaClass.id)
            )
            .where(
                arena_problem_set_problems.c.problem_id == problem_id,
                active.c.user_id == user_id,
                or_(ArenaProblemSet.starts_on.is_(None), ArenaProblemSet.starts_on <= now),
                or_(ArenaProblemSet.deadline.is_(None), ArenaProblemSet.deadline >= now),
            )
            .order_by(ArenaProblemSet.deadline.asc())
        )
    ).all()
    if not all_rows:
        return None
    row = all_rows[0]
    return AcceptingSetInfo(
        set_id=row.id,
        name=row.name,
        class_id=row.class_id,
        class_name=row.class_name,
        deadline=row.deadline,
        has_others=len(all_rows) > 1,
    )


async def problem_in_any_set_for_user(session: AsyncSession, *, problem_id: str, user_id: str) -> bool:
    """Return True when the problem is in any set of a class the user is active in."""
    active = _active_members_subquery().subquery()
    found = await session.scalar(
        select(ArenaProblemSet.id)
        .select_from(
            arena_problem_set_problems.join(
                ArenaProblemSet, arena_problem_set_problems.c.problem_set_id == ArenaProblemSet.id
            ).join(active, active.c.class_id == ArenaProblemSet.class_id)
        )
        .where(
            arena_problem_set_problems.c.problem_id == problem_id,
            active.c.user_id == user_id,
        )
        .limit(1)
    )
    return found is not None


async def find_problem_sets_exceeding_deadline(
    session: AsyncSession, *, class_id: str, cutoff: datetime
) -> list[ArenaProblemSet]:
    """Return problem sets whose deadline exceeds cutoff (for pre-flight truncation check)."""
    rows = await session.execute(
        select(ArenaProblemSet).where(
            ArenaProblemSet.class_id == class_id,
            ArenaProblemSet.deadline.is_not(None),
            ArenaProblemSet.deadline > cutoff,
        )
    )
    return list(rows.scalars())


async def truncate_problem_set_deadlines(session: AsyncSession, *, class_id: str, cutoff: datetime) -> None:
    """Set deadline = cutoff for all problem sets in the class whose deadline exceeds it."""
    await session.execute(
        update(ArenaProblemSet)
        .where(
            ArenaProblemSet.class_id == class_id,
            ArenaProblemSet.deadline.is_not(None),
            ArenaProblemSet.deadline > cutoff,
        )
        .values(deadline=cutoff)
    )
    await session.flush()
