#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Submission/judging timeline event builders."""

from __future__ import annotations

from collections.abc import Sequence

from shared.enumerations import JudgmentStatus
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import (
    HumanSubmissionConfirmation,
    Submission,
    SubmissionJudgment,
    SubmissionJudgmentAudit,
)
from web.models.users import User

from .common import (
    EventKind,
    SubmissionContext,
    TimelineEvent,
    actor_label,
    problem_ref,
    timestamp_or_fallback,
)


def confirmation_by_judgment(
    confirmations: list[HumanSubmissionConfirmation],
) -> dict[str, list[HumanSubmissionConfirmation]]:
    """Group confirmations by judgment id."""
    grouped: dict[str, list[HumanSubmissionConfirmation]] = {}
    for confirmation in confirmations:
        grouped.setdefault(confirmation.judgment_id, []).append(confirmation)
    for judgment_id in grouped:
        grouped[judgment_id].sort(key=lambda item: (item.created_at, item.id))
    return grouped


def build_submission_contexts(
    submissions: Sequence[Submission],
    users_by_id: dict[str, User],
    problems_by_id: dict[str, Problem],
) -> dict[str, SubmissionContext]:
    """Precompute submission text fragments used by multiple events."""
    contexts: dict[str, SubmissionContext] = {}
    for submission in submissions:
        team = users_by_id.get(submission.team_id)
        problem = problems_by_id.get(submission.problem_id)
        contexts[submission.id] = SubmissionContext(
            submission=submission,
            team_label=actor_label(team),
            problem_ref=problem_ref(problem),
        )
    return contexts


def judgment_start_audit(judgment: SubmissionJudgment) -> SubmissionJudgmentAudit | None:
    """Return the earliest persisted audit that marks autojudge work as started."""
    audits = sorted(judgment.audit_logs, key=lambda item: (item.created_at, item.id))
    dispatched = next((audit for audit in audits if audit.to_status == JudgmentStatus.DISPATCHED), None)
    if dispatched is not None:
        return dispatched
    return next((audit for audit in audits if audit.to_status == JudgmentStatus.JUDGING), None)


def has_submission_created_audit(judgment: SubmissionJudgment) -> bool:
    """Whether this judgment was created by a team submission rather than a requeue."""
    return any(audit.event_type == "SUBMISSION_CREATED" for audit in judgment.audit_logs)


def confirmation_finalized_by_two_equal(
    audit: SubmissionJudgmentAudit,
    judgment_confirmations: list[HumanSubmissionConfirmation],
) -> bool:
    """Return whether a final verdict audit came from two equal non-chief confirmations."""
    if audit.from_verdict is not None or audit.to_verdict is None:
        return False
    eligible = [
        item
        for item in judgment_confirmations
        if item.created_at <= audit.created_at and not item.is_chief_confirmation
    ]
    if len(eligible) < 2:
        return False
    matching = [item for item in eligible if item.confirmed_verdict == audit.to_verdict]
    if len(matching) < 2:
        return False
    return all(not item.is_chief_confirmation for item in eligible)


def judgment_final_verdict_audits(judgment: SubmissionJudgment) -> list[SubmissionJudgmentAudit]:
    """Return audits that persist a first final verdict for a human-reviewed judgment."""
    audits = sorted(judgment.audit_logs, key=lambda item: (item.created_at, item.id))
    return [
        audit
        for audit in audits
        if audit.event_source == "model_hook"
        and audit.event_type == "updated"
        and audit.from_verdict is None
        and audit.to_verdict is not None
    ]


def build_submission_events(
    *,
    contest: Contest,
    submissions: Sequence[Submission],
    users_by_id: dict[str, User],
    contexts: dict[str, SubmissionContext],
    confirmations_by_judgment: dict[str, list[HumanSubmissionConfirmation]],
) -> list[TimelineEvent]:
    """Collect submission/judging events from persisted submission state."""
    events: list[TimelineEvent] = []
    sequence = 0

    for submission in sorted(submissions, key=lambda item: (item.timestamp_seconds, item.created_at, item.id)):
        context = contexts[submission.id]
        team = users_by_id.get(submission.team_id)
        events.append(
            TimelineEvent(
                timestamp_seconds=submission.timestamp_seconds,
                created_at=submission.created_at,
                sequence=sequence,
                actor=actor_label(team),
                what=f"Team submits code for {context.problem_ref}",
                kind=EventKind.SUBMISSION_CREATED,
            )
        )
        sequence += 1

        judgments = sorted(submission.judgments, key=lambda item: (item.created_at, item.id))
        for judgment_index, judgment in enumerate(judgments):
            start_audit = judgment_start_audit(judgment)
            if start_audit is not None:
                events.append(
                    TimelineEvent(
                        timestamp_seconds=timestamp_or_fallback(
                            contest,
                            start_audit.timestamp_seconds,
                            start_audit.created_at,
                        ),
                        created_at=start_audit.created_at,
                        sequence=sequence,
                        actor="System",
                        what=f"Autojudge starts judging queued run for {context.problem_ref}",
                        kind=EventKind.AUTOJUDGE_START,
                    )
                )
                sequence += 1

            done_audits = sorted(
                [audit for audit in judgment.audit_logs if audit.to_status == JudgmentStatus.DONE],
                key=lambda item: (item.created_at, item.id),
            )
            if done_audits:
                done_audit = done_audits[0]
                verdict_label = done_audit.to_verdict.value if done_audit.to_verdict is not None else "?"
                events.append(
                    TimelineEvent(
                        timestamp_seconds=timestamp_or_fallback(
                            contest,
                            done_audit.timestamp_seconds,
                            done_audit.created_at,
                        ),
                        created_at=done_audit.created_at,
                        sequence=sequence,
                        actor="System",
                        what=f"Autojudge ends judging queued run with {verdict_label} for {context.problem_ref}",
                        kind=EventKind.AUTOJUDGE_END,
                    )
                )
                sequence += 1

                if contest.autojudge_only and done_audit.to_verdict is not None:
                    what = f"Submission gets final verdict {done_audit.to_verdict.value} for {context.problem_ref}"
                    events.append(
                        TimelineEvent(
                            timestamp_seconds=timestamp_or_fallback(
                                contest,
                                done_audit.timestamp_seconds,
                                done_audit.created_at,
                            ),
                            created_at=done_audit.created_at,
                            sequence=sequence,
                            actor="System",
                            what=(
                                "Autojudge issues final verdict "
                                f"{done_audit.to_verdict.value} for {context.problem_ref}"
                            ),
                            kind=EventKind.AUTOJUDGE_FINAL,
                        )
                    )
                    sequence += 1
                    events.append(
                        TimelineEvent(
                            timestamp_seconds=timestamp_or_fallback(
                                contest,
                                done_audit.timestamp_seconds,
                                done_audit.created_at,
                            ),
                            created_at=done_audit.created_at,
                            sequence=sequence,
                            actor="System",
                            what=what,
                            kind=EventKind.FINAL_SET,
                        )
                    )
                    sequence += 1

            if judgment_index > 0 and not has_submission_created_audit(judgment):
                events.append(
                    TimelineEvent(
                        timestamp_seconds=judgment.timestamp_seconds or 0,
                        created_at=judgment.created_at,
                        sequence=sequence,
                        actor="System",
                        what=f"Submission is requeued for autojudge for {context.problem_ref}",
                        kind=EventKind.REQUEUE,
                    )
                )
                sequence += 1

            if contest.autojudge_only:
                continue

            judgment_confirmations = confirmations_by_judgment.get(judgment.id, [])
            for confirmation in judgment_confirmations:
                actor = users_by_id.get(confirmation.judge_id)
                verdict_label = confirmation.confirmed_verdict.value
                if confirmation.is_chief_confirmation:
                    what = f"Chief judge sets verdict {verdict_label} for {context.problem_ref}"
                    kind = EventKind.CHIEF_SET
                else:
                    what = f"Judge confirms autojudge verdict {verdict_label} for {context.problem_ref}"
                    kind = EventKind.HUMAN_CONFIRM
                events.append(
                    TimelineEvent(
                        timestamp_seconds=confirmation.timestamp_seconds or 0,
                        created_at=confirmation.created_at,
                        sequence=sequence,
                        actor=actor_label(actor),
                        what=what,
                        kind=kind,
                    )
                )
                sequence += 1

            for audit in judgment_final_verdict_audits(judgment):
                timestamp_seconds = timestamp_or_fallback(contest, audit.timestamp_seconds, audit.created_at)
                verdict_label = audit.to_verdict.value if audit.to_verdict is not None else "?"
                if confirmation_finalized_by_two_equal(audit, judgment_confirmations):
                    events.append(
                        TimelineEvent(
                            timestamp_seconds=timestamp_seconds,
                            created_at=audit.created_at,
                            sequence=sequence,
                            actor="System",
                            what=(
                                f"Final verdict {verdict_label} is derived after two equal "
                                f"human confirmations for {context.problem_ref}"
                            ),
                            kind=EventKind.FINAL_DERIVED,
                        )
                    )
                    sequence += 1
                events.append(
                    TimelineEvent(
                        timestamp_seconds=timestamp_seconds,
                        created_at=audit.created_at,
                        sequence=sequence,
                        actor="System",
                        what=f"Submission gets final verdict {verdict_label} for {context.problem_ref}",
                        kind=EventKind.FINAL_SET,
                    )
                )
                sequence += 1

            overrides = sorted(judgment.overrides, key=lambda item: (item.created_at, item.id))
            for override in overrides:
                chief = users_by_id.get(override.overridden_by)
                events.append(
                    TimelineEvent(
                        timestamp_seconds=override.timestamp_seconds or 0,
                        created_at=override.created_at,
                        sequence=sequence,
                        actor=actor_label(chief),
                        what=(
                            f"Chief judge overrides final verdict from {override.original_verdict.value} "
                            f"to {override.new_verdict.value} for {context.problem_ref}"
                        ),
                        kind=EventKind.OVERRIDE,
                    )
                )
                sequence += 1

    return events
