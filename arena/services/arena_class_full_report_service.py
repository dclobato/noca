#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Class-wide problem-set report for Arena teachers.

Where ``build_teacher_problem_set_report`` shows one set's students x problems
matrix, this service builds the class-wide students x *problem sets* matrix: one
column per closed problem set, each cell holding that student's AC rate on that
set, plus a weighted total.

Only sets whose deadline has already passed take part, so the report describes
finished work. Columns are numbered from 1 in deadline-descending order; the page
renders those numbers in the matrix header and expands them in a legend table.

The AC rate of a cell is the share of the set's problems the student got accepted.
Which problems those are comes from ``set_tied_verdicts_for_sets``, the same
aggregation the per-set report uses, so the two reports cannot disagree: one
verdict per submission taken from its active judgment, and only for problems the
set currently contains. The total is the per-set rates weighted by problem count,
which is simply the student's accepted problems over every problem in the report.

All queries are batched across the whole class: adding a problem set or a student
does not add a query. The caller owns the transaction boundary; nothing here
writes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_classes import ArenaClass
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.services.arena_class_service import _active_members_subquery
from arena.services.arena_problem_set_report_service import best_verdict
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    _as_utc,
    _assert_teacher,
    set_tied_verdicts_for_sets,
)
from shared.db_schema.arena import (
    arena_problem_set_problems,
    arena_users,
)
from shared.enumerations import ArenaRole, Verdict

#: Bins in the per-set AC-rate distribution, spanning 0-100% in equal steps.
HISTOGRAM_BINS = 10


@dataclass(frozen=True)
class FullReportSetColumn:
    """One problem-set column of the class report, as shown in the legend."""

    index: int
    set_id: str
    name: str
    deadline: datetime
    problem_count: int
    histogram: tuple[int, ...] = ()
    """Student counts per AC-rate bin, ``HISTOGRAM_BINS`` wide; empty when the
    set has no problems and therefore no rates to distribute."""


@dataclass(frozen=True)
class FullReportCell:
    """One student's result on one problem set."""

    ac_count: int
    problem_count: int
    ac_rate: float | None


@dataclass(frozen=True)
class FullReportStudentRow:
    """One student row of the class report matrix."""

    user_id: str
    user_name: str
    cells: tuple[FullReportCell, ...]
    total_ac_count: int
    total_problem_count: int
    total_rate: float | None


@dataclass(frozen=True)
class ClassFullReport:
    """UI-ready data for the class-wide problem-set report page."""

    class_id: str
    class_name: str
    sets: tuple[FullReportSetColumn, ...]
    students: tuple[FullReportStudentRow, ...]
    total_problem_count: int
    set_average_rates: tuple[float | None, ...]
    class_average_rate: float | None
    histogram_max: int = 0
    """Tallest bin across every set's histogram. The legend charts share it as a
    fixed y-axis maximum so their bar heights are comparable set to set rather
    than each being scaled to its own peak."""


def _rate(ac_count: int, problem_count: int) -> float | None:
    """Return the AC percentage, or None when there is nothing to measure."""
    if problem_count <= 0:
        return None
    return ac_count / problem_count * 100


def _histogram(rates: Iterable[float | None]) -> tuple[int, ...]:
    """Bin AC rates into ``HISTOGRAM_BINS`` equal buckets spanning 0-100%.

    Bin ``i`` covers ``[i*10, (i+1)*10)`` percent, except the last, which is
    closed so a perfect 100% lands there rather than falling off the end. Rates
    of ``None`` (a set with no problems) contribute nothing.

    Args:
        rates: The per-student AC rates for one problem set.

    Returns:
        tuple: ``HISTOGRAM_BINS`` counts, or ``()`` when no rate was measurable.
    """
    counts = [0] * HISTOGRAM_BINS
    measured = False
    for rate in rates:
        if rate is None:
            continue
        measured = True
        counts[min(int(rate / (100 / HISTOGRAM_BINS)), HISTOGRAM_BINS - 1)] += 1
    return tuple(counts) if measured else ()


def _build_cell(ac_count: int, column: FullReportSetColumn) -> FullReportCell:
    """Build one matrix cell, clamping the AC count to the set's problem count.

    A problem removed from the set after being accepted would otherwise leave a
    stale count behind, so a rate can never exceed 100%.
    """
    clamped = min(ac_count, column.problem_count)
    return FullReportCell(
        ac_count=clamped,
        problem_count=column.problem_count,
        ac_rate=_rate(clamped, column.problem_count),
    )


async def _load_closed_sets(
    session: AsyncSession,
    *,
    class_id: str,
    now: datetime,
) -> list[ArenaProblemSet]:
    """Return the class problem sets whose deadline has passed, newest first."""
    rows = await session.execute(
        select(ArenaProblemSet)
        .where(
            ArenaProblemSet.class_id == class_id,
            ArenaProblemSet.deadline.is_not(None),
            ArenaProblemSet.deadline <= _as_utc(now),
        )
        .order_by(ArenaProblemSet.deadline.desc(), ArenaProblemSet.name.asc())
    )
    return list(rows.scalars())


async def _problem_counts(session: AsyncSession, set_ids: list[str]) -> dict[str, int]:
    """Map each problem-set id to how many problems it holds."""
    if not set_ids:
        return {}
    rows = await session.execute(
        select(
            arena_problem_set_problems.c.problem_set_id,
            func.count(arena_problem_set_problems.c.problem_id),
        )
        .where(arena_problem_set_problems.c.problem_set_id.in_(set_ids))
        .group_by(arena_problem_set_problems.c.problem_set_id)
    )
    return {set_id: int(count) for set_id, count in rows.all()}


async def _active_students(session: AsyncSession, class_id: str) -> list[tuple[str, str]]:
    """Return ``(user_id, name)`` for every active member of the class, by name."""
    active = _active_members_subquery().subquery()
    rows = await session.execute(
        select(arena_users.c.id, arena_users.c.nome)
        .select_from(active.join(arena_users, arena_users.c.id == active.c.user_id))
        .where(
            active.c.class_id == class_id,
            arena_users.c.ativo.is_(True),
        )
        .order_by(arena_users.c.nome.asc())
    )
    return [(row.id, row.nome) for row in rows.all()]


async def _accepted_problem_counts(
    session: AsyncSession,
    set_ids: list[str],
) -> dict[tuple[str, str], int]:
    """Map ``(user_id, problem_set_id)`` to the number of problems accepted.

    Delegates to the shared set-verdict aggregation so this report and the
    per-set report agree on what counts: one verdict per submission, from its
    active judgment, and only for problems the set currently contains.
    """
    grouped = await set_tied_verdicts_for_sets(session, set_ids)
    counts: dict[tuple[str, str], int] = {}
    for (set_id, user_id, _problem_id), verdicts in grouped.items():
        if best_verdict(verdicts) == Verdict.AC.value:
            counts[(user_id, set_id)] = counts.get((user_id, set_id), 0) + 1
    return counts


async def build_class_full_report(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    now: datetime,
) -> ClassFullReport:
    """Build the class-wide students x problem-sets AC-rate report.

    Args:
        session: Active database session; nothing is written.
        actor_id: The requesting user's id.
        actor_role: The requesting user's Arena role.
        class_id: The class to report on.
        now: Reference time deciding which problem sets are already closed.

    Returns:
        ClassFullReport: The legend columns, one row per active student, and the
        per-set and class-wide averages. A class with no closed sets or no
        students is a valid, empty report rather than an error.

    Raises:
        ArenaProblemSetNotFoundError: The class does not exist.
        ArenaProblemSetPermissionError: The actor neither owns the class nor is
            an Arena admin.
    """
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaProblemSetNotFoundError("Class does not exist.")
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)

    problem_sets = await _load_closed_sets(session, class_id=class_id, now=now)
    set_ids = [problem_set.id for problem_set in problem_sets]
    counts = await _problem_counts(session, set_ids)
    students = await _active_students(session, class_id)
    accepted = await _accepted_problem_counts(session, set_ids)

    columns = tuple(
        FullReportSetColumn(
            index=index,
            set_id=problem_set.id,
            name=problem_set.name,
            deadline=_as_utc(deadline),
            problem_count=counts.get(problem_set.id, 0),
        )
        for index, problem_set in enumerate(problem_sets, start=1)
        # The query filters out NULL deadlines; this narrows the type as well.
        if (deadline := problem_set.deadline) is not None
    )
    total_problem_count = sum(column.problem_count for column in columns)

    rows: list[FullReportStudentRow] = []
    for user_id, user_name in students:
        cells = tuple(_build_cell(accepted.get((user_id, column.set_id), 0), column) for column in columns)
        total_ac_count = sum(cell.ac_count for cell in cells)
        rows.append(
            FullReportStudentRow(
                user_id=user_id,
                user_name=user_name,
                cells=cells,
                total_ac_count=total_ac_count,
                total_problem_count=total_problem_count,
                total_rate=_rate(total_ac_count, total_problem_count),
            )
        )
    student_rows = tuple(rows)

    # The distribution needs the finished rows, so the columns are completed here
    # rather than carrying a half-built histogram through the row loop.
    columns = tuple(
        replace(column, histogram=_histogram(row.cells[position].ac_rate for row in student_rows))
        for position, column in enumerate(columns)
    )
    set_average_rates = tuple(
        _rate(
            sum(row.cells[position].ac_count for row in student_rows),
            column.problem_count * len(student_rows),
        )
        for position, column in enumerate(columns)
    )
    class_average_rate = _rate(
        sum(row.total_ac_count for row in student_rows),
        total_problem_count * len(student_rows),
    )
    return ClassFullReport(
        class_id=arena_class.id,
        class_name=arena_class.name,
        sets=columns,
        students=student_rows,
        total_problem_count=total_problem_count,
        set_average_rates=set_average_rates,
        class_average_rate=class_average_rate,
        histogram_max=max((max(column.histogram, default=0) for column in columns), default=0),
    )
