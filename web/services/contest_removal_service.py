#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Coordinated permanent removal of inactive contests."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from shared.db_schema import (
    clarification_reads,
    clarifications,
    contest_languages,
    contests,
    human_submission_confirmations,
    login_history,
    problem_categories_map,
    problem_custom_validators,
    problem_language_limits,
    problem_limit_change_batch_languages,
    problem_limit_change_batch_submissions,
    problem_limit_change_batches,
    problem_sample_interactions,
    problems,
    profiling_case_results,
    profiling_runs,
    sites,
    solution_test_case_results,
    solution_test_runs,
    submission_interactive_attempts,
    submission_judgment_audit,
    submission_judgments,
    submission_test_results,
    submissions,
    tasks,
    test_cases,
    users,
    users_media,
    verdict_overrides,
)
from shared.services.problem_export_cache import discard_cached_export
from shared.services.security_events import record_security_event
from shared.services.valkey_service import (
    ContestValkeyPurgeResult,
    ContestValkeyTargets,
    ValkeyRuntime,
)
from web.services.contest_removal_files import quarantine_problem_files

logger = logging.getLogger(__name__)


class ContestRemovalError(RuntimeError):
    """Base error for a contest removal that did not commit."""


class ContestRemovalNotFoundError(ContestRemovalError):
    """Raised when the requested contest does not exist."""


class ContestRemovalActiveError(ContestRemovalError):
    """Raised when the requested contest is still active."""


@dataclass(frozen=True, slots=True)
class ContestRemovalTargets:
    """Database and runtime identifiers owned by one contest."""

    contest_id: str
    problem_ids: frozenset[str]
    user_ids: frozenset[str]
    submission_ids: frozenset[str]
    judgment_ids: frozenset[str]
    profiling_run_ids: frozenset[str]
    solution_test_run_ids: frozenset[str]
    validation_ids: frozenset[str]
    task_ids: frozenset[str]
    clarification_ids: frozenset[str]
    batch_ids: frozenset[str]

    def valkey_targets(self) -> ContestValkeyTargets:
        """Build the strict shared Valkey purge contract."""
        return ContestValkeyTargets(
            contest_id=self.contest_id,
            judgment_ids=self.judgment_ids,
            profiling_run_ids=self.profiling_run_ids,
            solution_test_run_ids=self.solution_test_run_ids,
            validation_ids=self.validation_ids,
            task_ids=self.task_ids,
            clarification_ids=self.clarification_ids,
        )


@dataclass(frozen=True, slots=True)
class ContestRemovalResult:
    """Summary of one committed contest removal."""

    contest_id: str
    problems_removed: int
    users_removed: int
    submissions_removed: int
    valkey: ContestValkeyPurgeResult


async def _string_ids(session: AsyncSession, statement: Executable) -> frozenset[str]:
    """Execute a scalar select and normalize its non-null string identifiers."""
    values = (await session.execute(statement)).scalars()
    return frozenset(str(value) for value in values if value is not None)


async def _collect_targets(session: AsyncSession, contest_id: str) -> ContestRemovalTargets:
    """Collect every identifier needed for database, file, and runtime cleanup."""
    problem_ids = await _string_ids(session, select(problems.c.id).where(problems.c.contest_id == contest_id))
    user_ids = await _string_ids(session, select(users.c.id).where(users.c.contest_id == contest_id))
    submission_ids = await _string_ids(
        session,
        select(submissions.c.id).where(submissions.c.problem_id.in_(problem_ids)),
    )
    judgment_ids = await _string_ids(
        session,
        select(submission_judgments.c.id).where(submission_judgments.c.submission_id.in_(submission_ids)),
    )
    profiling_run_ids = await _string_ids(
        session,
        select(profiling_runs.c.id).where(profiling_runs.c.problem_id.in_(problem_ids)),
    )
    solution_test_run_ids = await _string_ids(
        session,
        select(solution_test_runs.c.id).where(solution_test_runs.c.problem_id.in_(problem_ids)),
    )
    validation_ids = await _string_ids(
        session,
        select(problem_custom_validators.c.candidate_token).where(
            problem_custom_validators.c.problem_id.in_(problem_ids),
            problem_custom_validators.c.candidate_token.is_not(None),
        ),
    )
    task_ids = await _string_ids(session, select(tasks.c.id).where(tasks.c.team_id.in_(user_ids)))
    clarification_ids = await _string_ids(
        session,
        select(clarifications.c.id).where(clarifications.c.team_id.in_(user_ids)),
    )
    batch_ids = await _string_ids(
        session,
        select(problem_limit_change_batches.c.id).where(problem_limit_change_batches.c.contest_id == contest_id),
    )
    return ContestRemovalTargets(
        contest_id=contest_id,
        problem_ids=problem_ids,
        user_ids=user_ids,
        submission_ids=submission_ids,
        judgment_ids=judgment_ids,
        profiling_run_ids=profiling_run_ids,
        solution_test_run_ids=solution_test_run_ids,
        validation_ids=validation_ids,
        task_ids=task_ids,
        clarification_ids=clarification_ids,
        batch_ids=batch_ids,
    )


async def _delete_contest_graph(session: AsyncSession, targets: ContestRemovalTargets) -> None:
    """Delete restrictive relationships before deleting the contest roots."""
    batch_ids = targets.batch_ids
    judgment_ids = targets.judgment_ids
    submission_ids = targets.submission_ids
    profiling_ids = targets.profiling_run_ids
    problem_ids = targets.problem_ids
    user_ids = targets.user_ids

    await session.execute(
        delete(problem_limit_change_batch_submissions).where(
            problem_limit_change_batch_submissions.c.batch_id.in_(batch_ids)
        )
    )
    await session.execute(
        delete(problem_limit_change_batch_languages).where(
            problem_limit_change_batch_languages.c.batch_id.in_(batch_ids)
        )
    )
    for table in (
        human_submission_confirmations,
        submission_test_results,
        submission_interactive_attempts,
        submission_judgment_audit,
    ):
        await session.execute(delete(table).where(table.c.judgment_id.in_(judgment_ids)))
    await session.execute(delete(verdict_overrides).where(verdict_overrides.c.submission_id.in_(submission_ids)))
    await session.execute(delete(submission_judgments).where(submission_judgments.c.id.in_(judgment_ids)))
    await session.execute(delete(submissions).where(submissions.c.id.in_(submission_ids)))

    await session.execute(
        delete(profiling_case_results).where(profiling_case_results.c.profiling_run_id.in_(profiling_ids))
    )
    await session.execute(delete(profiling_runs).where(profiling_runs.c.id.in_(profiling_ids)))

    solution_test_ids = targets.solution_test_run_ids
    await session.execute(
        delete(solution_test_case_results).where(
            solution_test_case_results.c.solution_test_run_id.in_(solution_test_ids)
        )
    )
    await session.execute(delete(solution_test_runs).where(solution_test_runs.c.id.in_(solution_test_ids)))
    await session.execute(
        delete(clarification_reads).where(clarification_reads.c.clarification_id.in_(targets.clarification_ids))
    )
    await session.execute(delete(clarifications).where(clarifications.c.id.in_(targets.clarification_ids)))
    await session.execute(delete(tasks).where(tasks.c.id.in_(targets.task_ids)))
    await session.execute(delete(problem_limit_change_batches).where(problem_limit_change_batches.c.id.in_(batch_ids)))

    for table in (
        problem_categories_map,
        problem_custom_validators,
        problem_language_limits,
        problem_sample_interactions,
        test_cases,
    ):
        await session.execute(delete(table).where(table.c.problem_id.in_(problem_ids)))

    await session.execute(delete(users_media).where(users_media.c.user_id.in_(user_ids)))
    await session.execute(delete(login_history).where(login_history.c.user_id.in_(user_ids)))
    await session.execute(delete(contest_languages).where(contest_languages.c.contest_id == targets.contest_id))
    await session.execute(delete(problems).where(problems.c.id.in_(problem_ids)))
    await session.execute(
        update(contests).where(contests.c.id == targets.contest_id).values(owner_user_id=None, chief_judge_id=None)
    )
    await session.execute(delete(users).where(users.c.id.in_(user_ids)))
    await session.execute(delete(sites).where(sites.c.contest_id == targets.contest_id))
    await session.execute(delete(contests).where(contests.c.id == targets.contest_id))


async def remove_inactive_contest(
    session: AsyncSession,
    *,
    contest_id: str,
    actor_uberadmin_id: str,
    actor_uberadmin_label: str | None = None,
    valkey_runtime: ValkeyRuntime,
    statement_dir: Path,
    testcase_dir: Path,
    export_cache_dir: Path | None = None,
) -> ContestRemovalResult:
    """Permanently remove one inactive contest across all NOCA-managed stores.

    Args:
        session: Active async database session.
        contest_id: Identifier of the inactive contest to remove.
        actor_uberadmin_id: Acting UberAdmin identifier.
        actor_uberadmin_label: Acting UberAdmin username, snapshotted into the
            security event so the viewer names the actor instead of showing an
            opaque identifier.
        valkey_runtime: Runtime used for strict Valkey state cleanup.
        statement_dir: Root directory holding problem statement files.
        testcase_dir: Root directory holding problem test-case files.
        export_cache_dir: Per-problem public export cache directory, when one
            is configured. Its entries are derived data, so they are dropped
            after the deletion commits rather than quarantined with the
            originals: a rollback leaves the problems in place, and their
            caches are still correct.

    Raises:
        ContestRemovalNotFoundError: If the contest does not exist.
        ContestRemovalActiveError: If the contest is active.
        ContestValkeyPurgeError: If strict runtime cleanup cannot be verified.
        ContestRemovalError: If filesystem or database deletion fails.
    """
    contest_row = (
        (
            await session.execute(
                select(contests.c.id, contests.c.active).where(contests.c.id == contest_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if contest_row is None:
        raise ContestRemovalNotFoundError("Contest not found.")
    if contest_row["active"]:
        raise ContestRemovalActiveError("Active contests cannot be removed.")

    targets = await _collect_targets(session, contest_id)
    valkey_result = await valkey_runtime.purge_contest_runtime_state(targets.valkey_targets())
    quarantine = quarantine_problem_files(
        targets.problem_ids,
        statement_dir=statement_dir,
        testcase_dir=testcase_dir,
    )
    try:
        await _delete_contest_graph(session, targets)
        await record_security_event(
            session,
            module="web",
            event_type="contest_deleted",
            severity="warning",
            actor_user_id=actor_uberadmin_id,
            actor_label=actor_uberadmin_label,
            metadata={"contest_id": contest_id},
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        quarantine.restore()
        if isinstance(exc, ContestRemovalError):
            raise
        raise ContestRemovalError("Contest database removal failed.") from exc

    quarantine.discard()
    if export_cache_dir is not None:
        for problem_id in sorted(targets.problem_ids):
            # Best-effort: the rows are gone either way, and a surviving file is
            # unreachable -- no route can name a problem that no longer exists.
            try:
                await discard_cached_export(export_cache_dir, problem_id)
            except OSError:
                logger.warning("Could not discard the cached export of removed problem %s", problem_id, exc_info=True)
    return ContestRemovalResult(
        contest_id=contest_id,
        problems_removed=len(targets.problem_ids),
        users_removed=len(targets.user_ids),
        submissions_removed=len(targets.submission_ids),
        valkey=valkey_result,
    )
