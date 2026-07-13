#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Worker database reads for pending validator candidate recovery."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select, update

from autojudge.db._base import _DatabaseBase, _utcnow
from autojudge.runtime_utils import decode_for_text_column
from autojudge.types import (
    ActiveCustomValidator,
    CustomValidatorDispatchState,
    RecoverableCustomValidatorJob,
)
from shared.db_schema import (
    arena_problem_custom_validators,
    arena_problems,
    arena_submission_interactive_attempts,
    arena_submission_judgments,
    arena_submissions,
    problem_custom_validators,
    submission_interactive_attempts,
)
from shared.enumerations import (
    ArenaNotificationKind,
    CustomValidatorActiveState,
    CustomValidatorCandidateState,
    JudgmentStatus,
    Verdict,
)
from shared.queue_schema import CustomValidatorValidationJob
from shared.services.arena_notification_service import create_arena_notification


class _CustomValidatorMixin(_DatabaseBase):
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
        """Load configured and active state at submission dispatch time."""
        table = arena_problem_custom_validators if domain == "arena" else problem_custom_validators
        row = (
            await self._conn.execute(
                select(
                    table.c.active_language_id,
                    table.c.active_source,
                    table.c.active_state,
                    table.c.candidate_source,
                ).where(table.c.problem_id == problem_id)
            )
        ).one_or_none()
        if row is None:
            return CustomValidatorDispatchState(False, None)
        configured = row.active_source is not None or row.candidate_source is not None
        active = None
        if row.active_state == CustomValidatorActiveState.VALID:
            active = ActiveCustomValidator(str(row.active_language_id), str(row.active_source))
        return CustomValidatorDispatchState(configured, active)

    async def insert_interactive_attempt(
        self,
        *,
        domain: str,
        judgment_id: str,
        attempt_number: int,
        result: Any,
    ) -> None:
        """Persist bounded diagnostics for one interactive attempt."""
        table = arena_submission_interactive_attempts if domain == "arena" else submission_interactive_attempts
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
        await self._conn.execute(
            table.insert().values(
                id=str(uuid.uuid4()),
                judgment_id=judgment_id,
                attempt_number=attempt_number,
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
