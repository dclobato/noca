#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Non-scoring solution-test runs for judges and admins.

Solution tests live in their own tables rather than behind a flag on
``submissions``, so leakage into standings, balloons, Runs, reports, feeds, and
exports is structurally impossible rather than test-enforced. Nothing in this
module publishes a verdict event or touches the scoreboard cache.

The run status reuses ``JudgmentStatus`` so the autojudge state machine is
reused verbatim; this deliberately couples staff tooling to submission status
semantics.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import (
    JudgmentStatus,
    RoleEnum,
)
from shared.queue_schema import SolutionTestJob
from shared.services.pagination_service import Pagination
from shared.services.problem_judgeability import judgeability_error
from web.models._base import _new_uuid
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.solution_test import SolutionTestRun
from web.models.users import UberAdmin, User
from web.services.problem_service import load_contest_problem_judgeability_facts
from web.services.rate_limit_service import check_solution_test_rate_limit
from web.services.valkey_service import enqueue_solution_test_job as _enqueue_solution_test_job

RUNS_PER_PAGE = 50


class SolutionTestRateLimitError(RuntimeError):
    """Raised when an actor exceeds their independent solution-test budget."""

    def __init__(self, next_allowed_at: datetime) -> None:
        super().__init__("Solution-test rate limit exceeded.")
        self.next_allowed_at = next_allowed_at


def actor_key(actor: User | UberAdmin) -> str:
    """Return the namespaced rate-limit key for a staff actor.

    Contest users and uberadmins are separate id spaces, so the key carries the
    space explicitly instead of relying on UUIDs never colliding.
    """
    return f"uberadmin:{actor.id}" if isinstance(actor, UberAdmin) else f"user:{actor.id}"


async def create_solution_test_run(
    session: AsyncSession,
    actor: User | UberAdmin,
    contest: Contest,
    *,
    problem_id: str,
    language_id: str,
    source_code: str,
    source_hash: str,
    source_size: int,
    rate_limit_window_seconds: int,
    rate_limit_max_runs: int,
) -> SolutionTestRun:
    """Validate and insert one queued solution-test run.

    Does NOT commit — the caller owns the transaction, mirroring
    ``create_submission``.

    Args:
        session: Active async SQLAlchemy session.
        actor: The JUDGE, ADMIN, or UBERADMIN triggering the run.
        contest: The contest that owns the problem.
        problem_id: UUID of the problem to test against.
        language_id: Language key for the candidate solution.
        source_code: Decoded source code.
        source_hash: SHA-256 hex digest of the source bytes.
        source_size: Size in bytes of the uploaded file.
        rate_limit_window_seconds: Rolling window length in seconds.
        rate_limit_max_runs: Maximum runs allowed within the window.

    Returns:
        The inserted, flushed SolutionTestRun.

    Raises:
        SolutionTestRateLimitError: If the actor exceeded their budget.
        ValueError: If the problem cannot be judged yet.
    """
    allowed, next_allowed_at = await check_solution_test_rate_limit(
        session, actor_key(actor), rate_limit_window_seconds, rate_limit_max_runs
    )
    if not allowed:
        assert next_allowed_at is not None
        raise SolutionTestRateLimitError(next_allowed_at)

    await _ensure_problem_in_contest(session, contest, problem_id)
    await _ensure_judgeable(session, problem_id)

    is_uberadmin = isinstance(actor, UberAdmin) or actor.role == RoleEnum.UBERADMIN
    run = SolutionTestRun(
        id=_new_uuid(),
        problem_id=problem_id,
        language_id=language_id,
        source_code=source_code,
        source_hash=source_hash,
        source_size_bytes=source_size,
        status=JudgmentStatus.QUEUED,
        triggered_by_user_id=None if is_uberadmin else actor.id,
        triggered_by_uberadmin_id=actor.id if is_uberadmin else None,
        # Snapshotted so attribution survives the account being deleted, the
        # same pattern security_events.actor_label uses.
        triggered_by_label=actor.username,
    )
    session.add(run)
    await session.flush()
    return run


async def _ensure_problem_in_contest(session: AsyncSession, contest: Contest, problem_id: str) -> None:
    """Raise ValueError when the problem does not belong to the supplied contest.

    The route already checks this before calling, but the scope must be enforced
    here too: without it a caller could create a run against one contest's problem
    and then enqueue it carrying a different contest's id, which is what the queue
    metrics and the Valkey purge contract key on.

    Args:
        session: Active async SQLAlchemy session.
        contest: The contest the run is being created under.
        problem_id: UUID of the target problem.

    Raises:
        ValueError: If the problem is missing or belongs to another contest.
    """
    owned = await session.scalar(
        select(func.count()).select_from(Problem).where(Problem.id == problem_id, Problem.contest_id == contest.id)
    )
    if not owned:
        raise ValueError("The selected problem does not belong to this contest.")


async def _ensure_judgeable(session: AsyncSession, problem_id: str) -> None:
    """Raise ValueError when the problem cannot be judged in its current state.

    Applies the same shared contract as the contestant submission gate, decided
    from the problem's stored strategy rather than from validator presence.

    Raises:
        ValueError: With the shared gate's operator-facing reason.
    """
    facts = await load_contest_problem_judgeability_facts(session, problem_id)
    reason = judgeability_error(facts)
    if reason is not None:
        raise ValueError(reason)


async def get_solution_test_run(
    session: AsyncSession,
    contest: Contest,
    run_id: str,
    *,
    restrict_to_user_id: str | None = None,
) -> SolutionTestRun | None:
    """Load one run of this contest, with its problem, language, and results.

    Args:
        session: Active async SQLAlchemy session.
        contest: Contest scoping the lookup.
        run_id: UUID of the run.
        restrict_to_user_id: When set, only runs triggered by that contest user
            resolve. A JUDGE fetching another's run therefore gets ``None``, and
            the route turns that into 404 rather than 403 so existence does not
            leak.

    Returns:
        The run, or None when it does not exist or is out of scope.
    """
    stmt = (
        select(SolutionTestRun)
        .join(Problem, SolutionTestRun.problem_id == Problem.id)
        .where(SolutionTestRun.id == run_id, Problem.contest_id == contest.id)
        .options(
            selectinload(SolutionTestRun.problem),
            selectinload(SolutionTestRun.language),
            selectinload(SolutionTestRun.triggered_by_user),
            selectinload(SolutionTestRun.triggered_by_uberadmin),
            selectinload(SolutionTestRun.case_results),
        )
    )
    if restrict_to_user_id is not None:
        stmt = stmt.where(SolutionTestRun.triggered_by_user_id == restrict_to_user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_solution_test_runs_paginated(
    session: AsyncSession,
    contest: Contest,
    *,
    page: int = 1,
    per_page: int = RUNS_PER_PAGE,
    problem_id: str | None = None,
    restrict_to_user_id: str | None = None,
) -> Pagination[SolutionTestRun]:
    """Return one page of this contest's solution-test runs, newest first.

    There is no ``contest_id`` column on the runs table: the contest scope comes
    from joining ``problems``, exactly as profiling does, and the composite
    ``(problem_id, created_at)`` index serves the query.

    Args:
        session: Active async SQLAlchemy session.
        contest: Contest scoping the listing.
        page: One-based page number.
        per_page: Rows per page.
        problem_id: Optional problem filter.
        restrict_to_user_id: When set, only that contest user's runs are listed.

    Returns:
        A Pagination of runs.
    """
    page = max(1, page)
    filters = [Problem.contest_id == contest.id]
    if problem_id:
        filters.append(SolutionTestRun.problem_id == problem_id)
    if restrict_to_user_id is not None:
        filters.append(SolutionTestRun.triggered_by_user_id == restrict_to_user_id)

    total = int(
        cast(
            int,
            await session.scalar(
                select(func.count())
                .select_from(SolutionTestRun)
                .join(Problem, SolutionTestRun.problem_id == Problem.id)
                .where(*filters)
            )
            or 0,
        )
    )
    rows = (
        await session.execute(
            select(SolutionTestRun)
            .join(Problem, SolutionTestRun.problem_id == Problem.id)
            .where(*filters)
            .options(
                selectinload(SolutionTestRun.problem),
                selectinload(SolutionTestRun.language),
                selectinload(SolutionTestRun.triggered_by_user),
                selectinload(SolutionTestRun.triggered_by_uberadmin),
            )
            .order_by(SolutionTestRun.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars()
    return Pagination(items=list(rows), page=page, per_page=per_page, total=total)


async def enqueue_solution_test_job(
    valkey: object,
    run: SolutionTestRun,
    contest: Contest,
    *,
    priority: bool,
) -> None:
    """Place a committed run on the judge queue.

    Solution tests use the contestant queues and the same
    ``priority=contest.is_running`` rule, so no new queue key or Lua change is
    involved.

    Args:
        valkey: ValkeyRuntime or raw client.
        run: The committed solution-test run.
        contest: The contest that owns the run's problem.
        priority: Whether to use the priority queue.
    """
    await _enqueue_solution_test_job(
        valkey,
        SolutionTestJob(
            solution_test_run_id=run.id,
            contest_id=contest.id,
            problem_id=run.problem_id,
            language_id=run.language_id,
        ),
        priority=priority,
    )
