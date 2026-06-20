#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Submission judging history builders."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, RoleEnum
from web.models import Contest, HumanSubmissionConfirmation, Submission, SubmissionJudgment, UberAdmin, User

from .types import JudgingHistoryEntry, JudgingHistoryResponse


async def get_judging_history(
    session: AsyncSession,
    submission_id: str,
    requesting_user: UberAdmin | User,
    contest: Contest,
) -> JudgingHistoryResponse:
    """Build the judging history for one submission."""
    result = await session.execute(
        select(Submission)
        .where(Submission.id == submission_id)
        .options(
            selectinload(Submission.judgments).selectinload(SubmissionJudgment.audit_logs),
            selectinload(Submission.judgments)
            .selectinload(SubmissionJudgment.confirmations)
            .selectinload(HumanSubmissionConfirmation.judge),
            selectinload(Submission.overrides),
            selectinload(Submission.problem),
        )
    )
    submission = result.scalar_one_or_none()
    if submission is None or submission.problem.contest_id != contest.id:
        raise HTTPException(status_code=404)

    if isinstance(requesting_user, User) and requesting_user.role == RoleEnum.TEAM:
        raise HTTPException(status_code=403)
    if not isinstance(requesting_user, UberAdmin) and requesting_user.role not in {
        RoleEnum.JUDGE,
        RoleEnum.ADMIN,
        RoleEnum.STAFF,
    }:
        raise HTTPException(status_code=403)

    entries: list[JudgingHistoryEntry] = []
    override_match_keys = {(override.judgment_id, override.new_verdict) for override in submission.overrides}
    actor_ids = {
        audit.actor_user_id for judgment in submission.judgments for audit in judgment.audit_logs if audit.actor_user_id
    }
    actor_ids.update(override.overridden_by for override in submission.overrides)
    actors_by_id: dict[str, User] = {}
    if actor_ids:
        actor_result = await session.execute(select(User).where(User.id.in_(actor_ids), User.contest_id == contest.id))
        actors = actor_result.scalars().all()
        actors_by_id = {actor.id: actor for actor in actors}

    def _actor_display(actor_id: str | None) -> str | None:
        if actor_id is None:
            return None
        actor = actors_by_id.get(actor_id)
        if actor is None:
            return actor_id
        return actor.fullname or actor.username

    for judgment in submission.judgments:
        for audit in judgment.audit_logs:
            verdict_changed = audit.from_verdict != audit.to_verdict
            if audit.event_type != "created" and not verdict_changed:
                continue
            if audit.event_type == "updated" and (audit.judgment_id, audit.to_verdict) in override_match_keys:
                continue
            if (
                audit.event_source == "model_hook"
                and audit.from_status == JudgmentStatus.DONE
                and audit.to_status == JudgmentStatus.DONE
                and audit.from_verdict is None
                and audit.to_verdict is not None
            ):
                continue
            if audit.to_status is None:
                continue

            kind: Literal["auto", "rejudge", "override", "confirmation", "chief_confirmation"]
            if audit.event_type == "created":
                kind = "auto"
                actor_id = None
            elif audit.event_source == "WEB":
                kind = "rejudge"
                actor_id = UUID(audit.actor_user_id) if audit.actor_user_id else None
            else:
                kind = "auto"
                actor_id = None

            verdict = audit.to_verdict if audit.to_verdict is not None else audit.from_verdict
            entries.append(
                JudgingHistoryEntry(
                    judgment_id=UUID(audit.judgment_id),
                    status=audit.to_status,
                    verdict=verdict,
                    kind=kind,
                    actor_id=actor_id,
                    actor_display=_actor_display(audit.actor_user_id),
                    timestamp=audit.created_at,
                    timestamp_seconds=audit.timestamp_seconds,
                    reason=None,
                )
            )

        for confirmation in judgment.confirmations:
            entries.append(
                JudgingHistoryEntry(
                    judgment_id=UUID(confirmation.judgment_id),
                    status=JudgmentStatus.DONE,
                    verdict=confirmation.confirmed_verdict,
                    kind="chief_confirmation" if confirmation.is_chief_confirmation else "confirmation",
                    actor_id=UUID(confirmation.judge_id),
                    actor_display=confirmation.judge.fullname or confirmation.judge.username,
                    timestamp=confirmation.created_at,
                    timestamp_seconds=confirmation.timestamp_seconds,
                    reason=None,
                )
            )

    for override in submission.overrides:
        entries.append(
            JudgingHistoryEntry(
                judgment_id=UUID(override.judgment_id),
                status=JudgmentStatus.DONE,
                verdict=override.new_verdict,
                kind="override",
                actor_id=UUID(override.overridden_by),
                actor_display=_actor_display(override.overridden_by),
                timestamp=override.created_at,
                timestamp_seconds=override.timestamp_seconds,
                reason=override.reason,
            )
        )

    entries.sort(key=lambda entry: entry.timestamp, reverse=True)
    return JudgingHistoryResponse(submission_id=UUID(submission.id), entries=entries)
