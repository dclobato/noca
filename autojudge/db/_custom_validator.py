#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Worker database reads for pending validator candidate recovery."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import delete, select, update

from autojudge.db._arena_solver import _ArenaSolverMixin
from autojudge.db._base import AttemptClaim, _utcnow
from autojudge.runtime_utils import decode_for_text_column
from autojudge.types import (
    ActiveCustomValidator,
    CustomValidatorDispatchState,
    JudgmentOwnershipLost,
    RecoverableCustomValidatorJob,
)
from shared.db_schema import (
    arena_problem_custom_validators,
    arena_problems,
    arena_submission_interactive_attempts,
    arena_submission_judgments,
    arena_submissions,
    problem_custom_validators,
    problems,
    solution_test_case_results,
    solution_test_runs,
    submission_interactive_attempts,
    submission_judgments,
    test_cases,
)
from shared.enumerations import (
    ArenaNotificationKind,
    CustomValidatorActiveState,
    CustomValidatorCandidateState,
    JudgmentStatus,
    ProblemValidatorType,
    Verdict,
)
from shared.queue_schema import CustomValidatorValidationJob
from shared.services.arena_notification_service import create_arena_notification


class _CustomValidatorMixin(_ArenaSolverMixin):
    """List committed candidates for queue reconciliation."""

    async def list_recoverable_custom_validator_jobs(self) -> list[RecoverableCustomValidatorJob]:
        """Return every `PENDING` Contest and Arena candidate."""
        jobs: list[RecoverableCustomValidatorJob] = []
        for domain, table in (
            ("contest", problem_custom_validators),
            ("arena", arena_problem_custom_validators),
        ):
            rows = await self._conn.execute(
                select(table.c.problem_id, table.c.candidate_token).where(
                    table.c.candidate_state == CustomValidatorCandidateState.PENDING,
                    table.c.candidate_token.is_not(None),
                )
            )
            for row in rows:
                token = str(row.candidate_token)
                jobs.append(
                    RecoverableCustomValidatorJob(
                        status=CustomValidatorCandidateState.PENDING.value,
                        payload=CustomValidatorValidationJob(
                            validation_id=token,
                            domain="arena" if domain == "arena" else "contest",
                            problem_id=str(row.problem_id),
                            candidate_token=token,
                        ),
                    )
                )
        return jobs

    async def get_custom_validator_dispatch_state(self, domain: str, problem_id: str) -> CustomValidatorDispatchState:
        """Load the problem's stored strategy and its validator availability.

        The strategy comes from the problem row, not from the validator table, so
        a stale validator row cannot make a standard problem interactive and a
        removed source cannot make an interactive problem standard. The validator
        table is still read, but only to answer "is there an active valid
        revision" for a problem the strategy already says is interactive.

        Args:
            domain: ``"contest"`` or ``"arena"`` identity domain.
            problem_id: The problem being dispatched.

        Returns:
            CustomValidatorDispatchState: The strategy, the active revision when
            one applies, and an unsupported reason for a strategy this build
            cannot judge.
        """
        is_arena = domain == "arena"
        problem_table = arena_problems if is_arena else problems
        table = arena_problem_custom_validators if is_arena else problem_custom_validators

        strategy_value = (
            await self._conn.execute(select(problem_table.c.validator_type).where(problem_table.c.id == problem_id))
        ).scalar_one_or_none()
        if strategy_value is None:
            # No such problem. Treat it as standard and let the caller's own
            # missing-problem handling report it.
            return CustomValidatorDispatchState(ProblemValidatorType.STANDARD, None)
        strategy = ProblemValidatorType(strategy_value)

        if strategy is ProblemValidatorType.OUTPUT_CHECKER:
            return CustomValidatorDispatchState(
                strategy,
                None,
                unsupported_reason="Output checker validation is not available in this build.",
            )
        if strategy is not ProblemValidatorType.INTERACTIVE:
            return CustomValidatorDispatchState(strategy, None)

        row = (
            await self._conn.execute(
                select(
                    table.c.active_language_id,
                    table.c.active_source,
                    table.c.active_state,
                ).where(table.c.problem_id == problem_id)
            )
        ).one_or_none()
        active = None
        if row is not None and row.active_state == CustomValidatorActiveState.VALID:
            active = ActiveCustomValidator(str(row.active_language_id), str(row.active_source))
        return CustomValidatorDispatchState(strategy, active)

    async def insert_interactive_attempt(
        self,
        *,
        domain: str,
        owner_id: str,
        attempt_number: int,
        result: Any,
        test_case_ordinal: int | None = None,
        attempt_target: Literal["submission", "solution_test"] = "submission",
        attempt_token: str | None = None,
    ) -> None:
        """Persist bounded diagnostics for one interactive attempt.

        Attempt 1 clears every earlier row of the owner, including the rows of an
        already-passed test case, so an owner only ever retains the attempts of
        the last executed case.

        ``domain`` selects the contest-vs-arena validator schema and cannot carry
        the solution-test axis too: a solution test always runs against a
        *contest* problem. ``attempt_target`` is that independent axis — it
        chooses whether the attempt belongs to a real judgment or to a non-scoring
        solution-test run.

        Args:
            domain: ``"contest"`` or ``"arena"`` identity domain.
            owner_id: Judgment UUID, or solution-test run UUID when
                ``attempt_target="solution_test"``.
            attempt_number: 1 or 2 (a validator crash is retried once).
            result: The InteractiveAttemptResult to persist.
            test_case_ordinal: 1-based ordinal of the parametrizing test case.
            attempt_target: Which table family owns this attempt.
            attempt_token: Claim stamped by this attempt's dispatch.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the run.
        """
        if attempt_target == "solution_test":
            await self._insert_solution_test_interactive_attempt(
                solution_test_run_id=owner_id,
                attempt_number=attempt_number,
                test_case_ordinal=test_case_ordinal,
                result=result,
                attempt_token=attempt_token,
            )
            return

        table = arena_submission_interactive_attempts if domain == "arena" else submission_interactive_attempts
        judgment_id = owner_id
        if attempt_token is not None:
            judgment_table = arena_submission_judgments if domain == "arena" else submission_judgments
            claim = AttemptClaim(judgment_table, judgment_id, attempt_token)
            if not await self._holds_claim(claim):
                # The delete below would drop the new owner's transcript, so a
                # lost claim has to stop this write before it starts.
                raise JudgmentOwnershipLost(
                    f"Discarded interactive attempt {attempt_number} for judgment {judgment_id}: "
                    "the judgment was claimed by another attempt"
                )
        delete_where = table.c.judgment_id == judgment_id
        if attempt_number != 1:
            delete_where = delete_where & (table.c.attempt_number == attempt_number)
        await self._conn.execute(delete(table).where(delete_where))
        validator_verdict = None
        if result.crash_reason is None and result.validator_exit_code is not None:
            validator_verdict = {
                0: Verdict.AC,
                1: Verdict.WA,
                2: Verdict.TLE,
                4: Verdict.PE,
            }.get(result.validator_exit_code, Verdict.RE)
        final_verdict = result.classification.verdict
        limit_outcome = final_verdict.value if final_verdict in {Verdict.MLE, Verdict.OLE} else None
        if final_verdict == Verdict.TLE and result.watchdog_stalled_side == "contestant":
            limit_outcome = Verdict.TLE.value
        await self._conn.execute(
            table.insert().values(
                id=str(uuid.uuid4()),
                judgment_id=judgment_id,
                attempt_number=attempt_number,
                test_case_ordinal=test_case_ordinal,
                contestant_exit_code=result.contestant_exit_code,
                contestant_signal=result.contestant_signal,
                validator_exit_code=result.validator_exit_code,
                validator_signal=result.validator_signal,
                transcript=result.transcript.as_dict() if result.transcript is not None else None,
                contestant_stderr_excerpt=decode_for_text_column(result.contestant_stderr_excerpt),
                validator_stderr_excerpt=decode_for_text_column(result.validator_stderr_excerpt),
                wall_time_ms=result.wall_time_ms,
                memory_kb=result.memory_kb,
                output_bytes=result.contestant_output_bytes,
                limit_outcome=limit_outcome,
                validator_verdict=validator_verdict,
                crash_reason=result.crash_reason,
                created_at=_utcnow(),
            )
        )
        await self._conn.commit()

    async def _insert_solution_test_interactive_attempt(
        self,
        *,
        solution_test_run_id: str,
        attempt_number: int,
        test_case_ordinal: int | None,
        result: Any,
        attempt_token: str | None = None,
    ) -> None:
        """Persist one interactive attempt of a non-scoring solution-test run.

        Interactive rows share ``solution_test_case_results`` with ordinary ones
        and are distinguished by a non-null ``attempt_number``, which is why the
        table carries two partial unique indexes instead of one constraint.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the run.
        """
        if attempt_token is not None and not await self._holds_claim(
            AttemptClaim(solution_test_runs, solution_test_run_id, attempt_token)
        ):
            # The delete below would drop the new owner's rows, so a lost claim
            # has to stop this write before it starts.
            raise JudgmentOwnershipLost(
                f"Discarded interactive attempt {attempt_number} for solution-test run "
                f"{solution_test_run_id}: the run was claimed by another attempt"
            )
        ordinal = test_case_ordinal if test_case_ordinal is not None else 1
        delete_where = solution_test_case_results.c.solution_test_run_id == solution_test_run_id
        if attempt_number != 1:
            delete_where = delete_where & (solution_test_case_results.c.attempt_number == attempt_number)
        await self._conn.execute(delete(solution_test_case_results).where(delete_where))
        # Resolve the parametrizing case so an interactive row is not indistinguishable
        # from one whose test case was deleted (test_case_id IS NULL means exactly that
        # to the UI). Stays NULL only if the case really is gone by the time we persist.
        test_case_id = (
            await self._conn.execute(
                select(test_cases.c.id)
                .select_from(
                    test_cases.join(
                        solution_test_runs,
                        solution_test_runs.c.problem_id == test_cases.c.problem_id,
                    )
                )
                .where(
                    solution_test_runs.c.id == solution_test_run_id,
                    test_cases.c.ordinal == ordinal,
                )
            )
        ).scalar_one_or_none()
        # The run itself is marked FAILED when the validator never exits cleanly;
        # the row still records the attempt, so a crash is stored as RE.
        verdict = result.classification.verdict or Verdict.RE
        validator_verdict = None
        if result.crash_reason is None and result.validator_exit_code is not None:
            validator_verdict = {
                0: Verdict.AC,
                1: Verdict.WA,
                2: Verdict.TLE,
                4: Verdict.PE,
            }.get(result.validator_exit_code, Verdict.RE)
        limit_outcome = verdict.value if verdict in {Verdict.MLE, Verdict.OLE} else None
        if verdict == Verdict.TLE and result.watchdog_stalled_side == "contestant":
            limit_outcome = Verdict.TLE.value
        await self._conn.execute(
            solution_test_case_results.insert().values(
                id=str(uuid.uuid4()),
                solution_test_run_id=solution_test_run_id,
                test_case_id=test_case_id,
                ordinal=ordinal,
                attempt_number=attempt_number,
                verdict=verdict,
                wall_time_ms=result.wall_time_ms,
                memory_kb=result.memory_kb,
                output_bytes=result.contestant_output_bytes,
                exit_code=result.contestant_exit_code,
                exit_signal=result.contestant_signal,
                input_excerpt=None,
                expected_output_excerpt=None,
                stdout_excerpt=None,
                stderr_excerpt=decode_for_text_column(result.contestant_stderr_excerpt),
                transcript=result.transcript.as_dict() if result.transcript is not None else None,
                validator_exit_code=result.validator_exit_code,
                validator_signal=result.validator_signal,
                validator_stderr_excerpt=decode_for_text_column(result.validator_stderr_excerpt),
                limit_outcome=limit_outcome,
                validator_verdict=validator_verdict,
                crash_reason=result.crash_reason,
                created_at=_utcnow(),
            )
        )
        await self._conn.commit()

    async def reject_exhausted_custom_validator_validation(
        self,
        *,
        domain: str,
        problem_id: str,
        candidate_token: str,
    ) -> bool:
        """Mark a validation candidate invalid after repeated worker reaper drops."""
        table = arena_problem_custom_validators if domain == "arena" else problem_custom_validators
        result = await self._conn.execute(
            update(table)
            .where(
                table.c.problem_id == problem_id,
                table.c.candidate_token == candidate_token,
                table.c.candidate_state == CustomValidatorCandidateState.PENDING,
            )
            .values(
                candidate_state=CustomValidatorCandidateState.INVALID,
                candidate_compile_log=(
                    "Custom validator validation was abandoned after repeated worker "
                    "timeouts or crashes. Upload the validator again to retry."
                ),
                candidate_validated_at=_utcnow(),
                updated_at=_utcnow(),
            )
        )
        await self._conn.commit()
        return bool(result.rowcount)

    async def contain_arena_validator_crash(self, problem_id: str, triggering_judgment_id: str) -> list[str]:
        """Disable a crashed validator/problem and fail other queued judgments."""
        now = _utcnow()
        await self._conn.execute(
            update(arena_problem_custom_validators)
            .where(arena_problem_custom_validators.c.problem_id == problem_id)
            .values(active_state=CustomValidatorActiveState.RUNTIME_FAILED, updated_at=now)
        )
        owner_id = await self._conn.scalar(
            update(arena_problems)
            .where(arena_problems.c.id == problem_id)
            .values(enabled=False, updated_at=now)
            .returning(arena_problems.c.owner_id)
        )
        queued_rows = await self._conn.execute(
            select(arena_submission_judgments.c.id)
            .select_from(
                arena_submission_judgments.join(
                    arena_submissions,
                    arena_submissions.c.id == arena_submission_judgments.c.submission_id,
                )
            )
            .where(
                arena_submissions.c.problem_id == problem_id,
                arena_submission_judgments.c.status == JudgmentStatus.QUEUED.value,
                arena_submission_judgments.c.id != triggering_judgment_id,
            )
        )
        queued_ids = [str(row.id) for row in queued_rows]
        if queued_ids:
            await self._conn.execute(
                update(arena_submission_judgments)
                .where(arena_submission_judgments.c.id.in_(queued_ids))
                .values(
                    status=JudgmentStatus.FAILED.value,
                    error_message="Custom validator disabled after repeated runtime failure.",
                    finished_at=now,
                )
            )
            # These judgments are now terminal without a verdict. Where one of
            # them replaced an Accepted judgment for a rejudge, its pair no
            # longer holds a live AC and must stop counting as solved.
            await self.reconcile_arena_solvers_for_judgments(queued_ids)
        if owner_id is not None:
            await create_arena_notification(
                self._conn,
                user_id=str(owner_id),
                notification_kind=ArenaNotificationKind.CUSTOM_VALIDATOR_DISABLED,
                title="Custom validator disabled",
                message="The validator failed twice without a clean exit. Upload new source.",
                target_url=f"/admin/problems/{problem_id}/edit",
                source_ref=f"custom-validator-disabled:{problem_id}",
                context={"problem_id": problem_id, "judgment_id": triggering_judgment_id},
            )
        await self._conn.commit()
        return queued_ids
