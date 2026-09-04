#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Web per-actor write throttles backed by PostgreSQL row counts.

Every limit here has the same shape: take a transaction-scoped advisory lock on
the actor, count the rows the actor already wrote, and decide. Two rules exist:

- a **rolling window** -- how many rows the actor created in the last
  ``window_seconds``; refusal carries the moment the oldest in-window row falls
  out, so the caller can name a time;
- an **open count** -- how many of the actor's rows are still unfinished or
  unanswered; refusal carries no time, because only staff action releases it.

Counting rows rather than a separate counter means a refused, duplicate, or
invalid request never consumes budget, and the check is exact across replicas
with no extra state to keep. The advisory lock makes the check and the INSERT
that follows atomic, so two concurrent posts cannot both read the same
pre-insert count; it is held until the caller's transaction ends. On
non-PostgreSQL databases (the SQLite test fixtures) the lock is a no-op and only
the counting is exercised.

Lock keys are namespaced per budget (``hashtext('sos_task:' || team_id)``), so
SOS spam never serializes against that team's submissions. ``hashtext`` is
32-bit, so two namespaced keys -- or a namespaced key and the legacy
unnamespaced submission key -- can collide; the only consequence is two
unrelated actors serializing against each other, never a wrong decision.

A maximum of ``0`` or less means "unlimited" and short-circuits before the lock
is taken, matching the ``contest.max_problem_file_size_bytes == 0`` idiom.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.clarification import clarifications
from shared.db_schema.contest import tasks
from shared.db_schema.solution_test import solution_test_runs
from shared.db_schema.submission import submissions
from shared.enumerations import TaskType

SOS_TASK_LOCK_NAMESPACE = "sos_task"
PRINT_TASK_LOCK_NAMESPACE = "print_task"
CLARIFICATION_LOCK_NAMESPACE = "clarification"


async def _acquire_scoped_lock(session: AsyncSession, namespace: str | None, key: str) -> None:
    """Take a transaction-scoped advisory lock on ``namespace:key``.

    Args:
        session: The async database session.
        namespace: Budget name, so distinct budgets never serialize against each
            other. ``None`` locks the bare key, which only the historical
            submission budget uses.
        key: The actor identity to lock on (usually a team id).
    """
    conn = await session.connection()
    if conn.dialect.name != "postgresql":
        return
    if namespace is None:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:namespace || ':' || :key))"),
        {"namespace": namespace, "key": key},
    )


async def _check_rolling_window(
    session: AsyncSession,
    *,
    lock_namespace: str | None,
    lock_key: str,
    timestamp_column: ColumnElement[datetime],
    filters: Sequence[ColumnElement[bool]],
    window_seconds: int,
    max_events: int,
) -> tuple[bool, datetime | None]:
    """Count the actor's rows inside the window and decide whether one more fits.

    Owns the whole sequence -- disabled check, advisory lock, count -- so no
    public wrapper can forget the lock.

    Args:
        session: The async database session.
        lock_namespace: Budget name for the advisory lock.
        lock_key: Actor identity to lock and count on.
        timestamp_column: The creation timestamp the window is measured on.
        filters: Boolean clauses selecting this actor's rows in one table.
        window_seconds: The window length in seconds.
        max_events: Maximum rows allowed inside the window; 0 or less is unlimited.

    Returns:
        ``(True, None)`` when another row fits, otherwise ``(False, next_allowed_at)``
        where ``next_allowed_at`` is when the oldest in-window row leaves the window.
    """
    if max_events <= 0:
        return True, None
    if lock_namespace is None:
        # The legacy submission budget locks through its own public entry point,
        # which callers and tests may replace independently of this module's core.
        await acquire_submission_rate_lock(session, lock_key)
    else:
        await _acquire_scoped_lock(session, lock_namespace, lock_key)
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=window_seconds)
    row = (
        await session.execute(
            select(func.count(), func.min(timestamp_column)).where(*filters, timestamp_column > cutoff)
        )
    ).one()
    count, oldest_in_window = int(row[0] or 0), row[1]
    if count < max_events:
        return True, None
    # Drivers that do not carry the timezone (SQLite) hand back a naive value;
    # the column is UTC, and callers format it with astimezone(), which would
    # otherwise read it as local time.
    if oldest_in_window is not None and oldest_in_window.tzinfo is None:
        oldest_in_window = oldest_in_window.replace(tzinfo=UTC)
    return False, (oldest_in_window or now) + timedelta(seconds=window_seconds)


async def _check_open_count(
    session: AsyncSession,
    *,
    lock_namespace: str | None,
    lock_key: str,
    filters: Sequence[ColumnElement[bool]],
    max_open: int,
) -> tuple[bool, int]:
    """Count the actor's still-open rows and decide whether one more fits.

    Owns the same sequence as :func:`_check_rolling_window`, for the rule shape
    that has no time bound and therefore no retry time.

    Args:
        session: The async database session.
        lock_namespace: Budget name for the advisory lock.
        lock_key: Actor identity to lock and count on.
        filters: Boolean clauses selecting this actor's open rows in one table.
        max_open: Maximum open rows allowed; 0 or less is unlimited.

    Returns:
        ``(allowed, open_count)``; ``open_count`` is 0 when the rule is disabled.
    """
    if max_open <= 0:
        return True, 0
    await _acquire_scoped_lock(session, lock_namespace, lock_key)
    open_count = int(await session.scalar(select(func.count()).where(*filters)) or 0)
    return open_count < max_open, open_count


async def acquire_submission_rate_lock(session: AsyncSession, team_id: str) -> None:
    """Acquire an exclusive advisory lock for a team's submission rate limit check.

    Kept on the historical unnamespaced key so a rolling deploy cannot let two
    replicas lock a team's submissions differently.

    Args:
        session: The async database session.
        team_id: The team ID to lock on.
    """
    await _acquire_scoped_lock(session, None, team_id)


async def check_submission_rate_limit(
    session: AsyncSession,
    team_id: str,
    window_seconds: int,
    max_submissions: int,
) -> tuple[bool, datetime | None]:
    """Check if a team is within their submission rate limit.

    Args:
        session: The async database session.
        team_id: The team ID to check.
        window_seconds: The time window in seconds.
        max_submissions: The maximum number of submissions allowed in the window.

    Returns:
        A tuple of (allowed, next_allowed_at) where:
        - allowed: True if the team is within their rate limit, False otherwise.
        - next_allowed_at: If allowed is True, this is None. If allowed is False,
          this is the earliest datetime when the team can submit again.
    """
    return await _check_rolling_window(
        session,
        lock_namespace=None,
        lock_key=team_id,
        timestamp_column=submissions.c.created_at,
        filters=[submissions.c.team_id == team_id],
        window_seconds=window_seconds,
        max_events=max_submissions,
    )


async def check_solution_test_rate_limit(
    session: AsyncSession,
    actor_key: str,
    window_seconds: int,
    max_runs: int,
) -> tuple[bool, datetime | None]:
    """Check whether a staff actor is within their solution-test rate limit.

    Solution tests get an independent budget: a judge's tests never consume a
    team's submission allowance and vice versa. The advisory lock is namespaced
    so it neither collides across the two actor id spaces (contest users and
    uberadmins) nor serializes against team submissions.

    Args:
        session: The async database session.
        actor_key: ``"user:<id>"`` or ``"uberadmin:<id>"``.
        window_seconds: The time window in seconds.
        max_runs: The maximum solution-test runs allowed in the window.

    Returns:
        A tuple of (allowed, next_allowed_at). ``next_allowed_at`` is None when
        allowed, otherwise the earliest datetime a new run may be started.
    """
    actor_column = (
        solution_test_runs.c.triggered_by_uberadmin_id
        if actor_key.startswith("uberadmin:")
        else solution_test_runs.c.triggered_by_user_id
    )
    return await _check_rolling_window(
        session,
        lock_namespace="solution_test",
        lock_key=actor_key,
        timestamp_column=solution_test_runs.c.created_at,
        filters=[actor_column == actor_key.split(":", 1)[1]],
        window_seconds=window_seconds,
        max_events=max_runs,
    )


def _team_task_filters(team_id: str, task_type: TaskType) -> list[ColumnElement[bool]]:
    """Select one team's tasks of one type.

    Scoping by type is what keeps balloon tasks -- created by the judging path,
    not by the team -- out of a team's own budgets.

    Args:
        team_id: The team whose tasks are counted.
        task_type: The task type the budget covers.

    Returns:
        The boolean clauses selecting those rows.
    """
    return [tasks.c.team_id == team_id, tasks.c.type == task_type]


async def check_sos_task_rate_limit(
    session: AsyncSession,
    team_id: str,
    window_seconds: int,
    max_tasks: int,
) -> tuple[bool, datetime | None]:
    """Check whether a team may create another SOS task inside the window.

    Args:
        session: The async database session.
        team_id: The team ID to check.
        window_seconds: The time window in seconds.
        max_tasks: Maximum SOS tasks allowed in the window; ``0`` or less is unlimited.

    Returns:
        A tuple of (allowed, next_allowed_at); ``next_allowed_at`` is None when allowed.
    """
    return await _check_rolling_window(
        session,
        lock_namespace=SOS_TASK_LOCK_NAMESPACE,
        lock_key=team_id,
        timestamp_column=tasks.c.created_at,
        filters=_team_task_filters(team_id, TaskType.SOS),
        window_seconds=window_seconds,
        max_events=max_tasks,
    )


async def check_open_sos_task_limit(
    session: AsyncSession,
    team_id: str,
    max_open: int,
) -> tuple[bool, int]:
    """Check how many unfinished SOS tasks a team already holds.

    A team with the maximum number of open SOS calls gains nothing from another
    one, so this cap needs no window: staff finishing a task releases it.

    Args:
        session: The async database session.
        team_id: The team ID to check.
        max_open: Maximum unfinished SOS tasks; ``0`` or less is unlimited.

    Returns:
        A tuple of (allowed, open_count). ``open_count`` is 0 when the rule is disabled.
    """
    return await _check_open_count(
        session,
        lock_namespace=SOS_TASK_LOCK_NAMESPACE,
        lock_key=team_id,
        filters=[*_team_task_filters(team_id, TaskType.SOS), tasks.c.finished_at.is_(None)],
        max_open=max_open,
    )


async def check_print_task_rate_limit(
    session: AsyncSession,
    team_id: str,
    window_seconds: int,
    max_tasks: int,
) -> tuple[bool, datetime | None]:
    """Check whether a team may create another print task inside the window.

    Args:
        session: The async database session.
        team_id: The team ID to check.
        window_seconds: The time window in seconds.
        max_tasks: Maximum print tasks allowed in the window; ``0`` or less is unlimited.

    Returns:
        A tuple of (allowed, next_allowed_at); ``next_allowed_at`` is None when allowed.
    """
    return await _check_rolling_window(
        session,
        lock_namespace=PRINT_TASK_LOCK_NAMESPACE,
        lock_key=team_id,
        timestamp_column=tasks.c.created_at,
        filters=_team_task_filters(team_id, TaskType.PRINT),
        window_seconds=window_seconds,
        max_events=max_tasks,
    )


def _team_clarification_filters(team_id: str) -> list[ColumnElement[bool]]:
    """Select one team's own clarification questions.

    Announcements are stored with their author as ``team_id``, so a judge later
    changed to TEAM would otherwise have their announcements counted here.

    Args:
        team_id: The team whose questions are counted.

    Returns:
        The boolean clauses selecting those rows.
    """
    return [clarifications.c.team_id == team_id, clarifications.c.is_announcement.is_(False)]


async def check_clarification_rate_limit(
    session: AsyncSession,
    team_id: str,
    window_seconds: int,
    max_requests: int,
) -> tuple[bool, datetime | None]:
    """Check whether a team may ask another clarification inside the window.

    Args:
        session: The async database session.
        team_id: The team ID to check.
        window_seconds: The time window in seconds.
        max_requests: Maximum clarifications allowed in the window; ``0`` or less is unlimited.

    Returns:
        A tuple of (allowed, next_allowed_at); ``next_allowed_at`` is None when allowed.
    """
    return await _check_rolling_window(
        session,
        lock_namespace=CLARIFICATION_LOCK_NAMESPACE,
        lock_key=team_id,
        timestamp_column=clarifications.c.created_at,
        filters=_team_clarification_filters(team_id),
        window_seconds=window_seconds,
        max_events=max_requests,
    )


async def check_open_clarification_limit(
    session: AsyncSession,
    team_id: str,
    max_open: int,
) -> tuple[bool, int]:
    """Check how many unanswered clarifications a team already holds.

    Hidden rows are excluded: hiding is how a judge dismisses a question, so such
    a row will never be answered and counting it would block the team forever.

    Args:
        session: The async database session.
        team_id: The team ID to check.
        max_open: Maximum unanswered clarifications; ``0`` or less is unlimited.

    Returns:
        A tuple of (allowed, open_count). ``open_count`` is 0 when the rule is disabled.
    """
    return await _check_open_count(
        session,
        lock_namespace=CLARIFICATION_LOCK_NAMESPACE,
        lock_key=team_id,
        filters=[
            *_team_clarification_filters(team_id),
            clarifications.c.answered_at.is_(None),
            clarifications.c.hidden.is_(False),
        ],
        max_open=max_open,
    )
