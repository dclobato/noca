#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from enum import StrEnum


class RoleEnum(StrEnum):
    """
    Role claim values
    """

    UBERADMIN = "UBERADMIN"  # full system access
    ADMIN = "ADMIN"  # can manage contest, judge, staff and teams; can view all submissions and results
    JUDGE = "JUDGE"  # can view all submissions (no team indication) and results; can judge and answer clarifications
    STAFF = "STAFF"  # can view all results; cannot see or judge submissions; can handle task queue
    TEAM = "TEAM"  # can view own submissions and results; can submit; can request/view clarifications
    USER = "USER"  # can view running contests and scoreboard; cannot submit or view submissions


ALL_CONTEST_ROLES = (
    RoleEnum.ADMIN,
    RoleEnum.JUDGE,
    RoleEnum.STAFF,
    RoleEnum.TEAM,
    RoleEnum.USER,
)


class Environment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class Verdict(StrEnum):
    """
    All possible outcomes of judging a submission against a single test case
    or as a final aggregated result.

    Ordering in VERDICT_PRIORITY determines which verdict wins when aggregating
    across multiple test cases.
    """

    AC = "AC"  # Accepted
    PE = "PE"  # Presentation Error (correct answer, wrong formatting)
    WA = "WA"  # Wrong Answer
    TLE = "TLE"  # Time Limit Exceeded
    MLE = "MLE"  # Memory Limit Exceeded (cgroup OOM kill)
    OLE = "OLE"  # Output Limit Exceeded
    RE = "RE"  # Runtime Error (non-zero exit, signal, crash)
    CE = "CE"  # Compilation Error


VERDICT_LABELS: dict[str, str] = {
    Verdict.AC.value: "Accepted",
    Verdict.PE.value: "Presentation Error",
    Verdict.WA.value: "Wrong Answer",
    Verdict.TLE.value: "Time Limit Exceeded",
    Verdict.MLE.value: "Memory Limit Exceeded",
    Verdict.OLE.value: "Output Limit Exceeded",
    Verdict.RE.value: "Runtime Error",
    Verdict.CE.value: "Compilation Error",
}

VERDICT_BADGE_CLASSES: dict[str, str] = {
    Verdict.AC.value: "bg-success",
    Verdict.PE.value: "bg-danger",
    Verdict.WA.value: "bg-danger",
    Verdict.TLE.value: "bg-warning text-dark",
    Verdict.MLE.value: "bg-warning text-dark",
    Verdict.OLE.value: "bg-warning text-dark",
    Verdict.RE.value: "bg-warning text-dark",
    Verdict.CE.value: "bg-secondary",
}


# ---------------------------------------------------------------------------
# Verdict priority
# ---------------------------------------------------------------------------

# When aggregating across multiple test cases, the first verdict in this list
# that appears anywhere takes precedence. CE is listed first even though it
# is set before test cases run - it is included for completeness and safety.
VERDICT_PRIORITY: list[Verdict] = [
    Verdict.CE,
    Verdict.RE,
    Verdict.TLE,
    Verdict.MLE,
    Verdict.OLE,
    Verdict.WA,
    Verdict.PE,
    Verdict.AC,
]


class JudgmentStatus(StrEnum):
    """
    Lifecycle states of a submission_judgment record.
    Transitions are strictly forward: QUEUED -> DISPATCHED -> JUDGING -> DONE.
    FAILED and SUPERSEDED are terminal states that do not produce a verdict.
    """

    QUEUED = "QUEUED"  # inserted into DB, enqueued in Redis
    DISPATCHED = "DISPATCHED"  # picked up by a worker, moved to inflight list
    JUDGING = "JUDGING"  # worker has acquired container, running test cases
    DONE = "DONE"  # verdict written, result published
    FAILED = "FAILED"  # internal judge error (not contestant's fault)
    SUPERSEDED = "SUPERSEDED"  # replaced by a rejudge


class ProfilingStatus(StrEnum):
    """Lifecycle states of a profiling run."""

    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class ContestStatus(StrEnum):
    """
    Lifecycle states of a contest.
    Transitions are strictly forward: DRAFT → ACTIVE → FROZEN → ENDED → PAST.
    """

    DRAFT = "DRAFT"  # not yet started; visible only to admin
    ACTIVE = "ACTIVE"  # accepting submissions; scoreboard and submission results updating in real time
    FROZEN = "FROZEN"  # scoreboard frozen; stop pushing submission results to teams; still accepting submissions
    ENDED = "ENDED"  # no more submissions accepted; scoreboard and submission at same state of FROZEN
    PAST = "PAST"  # manually set by admin; scoreboard and submission results are unfrozen


class TaskType(StrEnum):
    """
    Types of background tasks that can be enqueued in the task queue.
    """

    BALLOON = "BALLOON"  # task to prepare and deliver a balloon for a judged submission
    FIRST_BALLOON = "FIRST_BALLOON"  # task to deliver the first-solve balloon for a problem
    PRINT = "PRINT"  # task to prepare and deliver a printout for a team
    SOS = "SOS"  # task requesting local assistance for a team in distress (e.g. computer failure, medical emergency)


class ArenaRole(StrEnum):
    """
    Role claim values for Arena users.
    """

    ARENA_ADMIN = "ARENA_ADMIN"  # full arena management access
    ARENA_JUDGE = "ARENA_JUDGE"  # reserved: future manual review interface
    ARENA_USER = "ARENA_USER"  # regular arena participant


class ArenaClassMembershipStatus(StrEnum):
    """Current state of a user's membership in an Arena class."""

    ACTIVE = "ACTIVE"  # user is currently enrolled in the class
    REMOVED = "REMOVED"  # user has been removed (self or by teacher/admin)


class ArenaClassRegistrationStatus(StrEnum):
    """Lifecycle of a self-service class registration request."""

    PENDING = "PENDING"  # awaiting teacher/admin decision
    APPROVED = "APPROVED"  # accepted; an ACTIVE membership was created
    DENIED = "DENIED"  # rejected by teacher/admin


class ArenaNotificationKind(StrEnum):
    """
    Durable notification categories emitted by Arena-adjacent workers.
    """

    SUBMISSION_JUDGED = "SUBMISSION_JUDGED"
    AI_REVIEW_COMPLETED = "AI_REVIEW_COMPLETED"
    AI_REVIEW_FAILED = "AI_REVIEW_FAILED"
    CLASS_REGISTRATION_REQUEST = "CLASS_REGISTRATION_REQUEST"
    CLASS_REGISTRATION_APPROVED = "CLASS_REGISTRATION_APPROVED"
    CLASS_REGISTRATION_DENIED = "CLASS_REGISTRATION_DENIED"
    CLASS_MEMBERSHIP_ADDED = "CLASS_MEMBERSHIP_ADDED"
    CLASS_MEMBERSHIP_REMOVED = "CLASS_MEMBERSHIP_REMOVED"
    PROBLEM_REMOVAL_REQUEST = "PROBLEM_REMOVAL_REQUEST"
    TEACHER_FEEDBACK_POSTED = "TEACHER_FEEDBACK_POSTED"
    OTHER = "OTHER"


class ArenaAIBatchJobStatus(StrEnum):
    """Local state machine for ``arena_ai_batch_jobs`` rows.

    These statuses are local to NOCA and do not mirror OpenAI verbatim.
    The ``openai_status`` column on the table holds the raw OpenAI value.
    """

    STAGED = "staged"  # Accepted; waiting for the batch flusher window to fire
    PREPARING = "preparing"  # Local files and JSONL are being prepared
    SUBMITTED = "submitted"  # Batch created at OpenAI; awaiting first poll
    POLLING = "polling"  # Poller has picked this up and is checking periodically
    EXPIRING = "expiring"  # Transient in-transaction claim sentinel for stale expiry
    COMPLETED = "completed"  # Terminal — OpenAI completed AND all results stored
    FAILED = "failed"  # Terminal — OpenAI failed, or per-request failure
    EXPIRED = "expired"  # Terminal — OpenAI 24 h window expired
    CANCELLED = "cancelled"  # Terminal — cancelled at OpenAI


ARENA_AI_BATCH_JOB_TERMINAL_STATUSES: frozenset[str] = frozenset(
    s.value
    for s in (
        ArenaAIBatchJobStatus.COMPLETED,
        ArenaAIBatchJobStatus.FAILED,
        ArenaAIBatchJobStatus.EXPIRED,
        ArenaAIBatchJobStatus.CANCELLED,
    )
)

# Maps each notification kind to a Material Symbols icon name rendered in the UI.
# The fallback icon used when a kind is absent from this dict is "notifications".
ARENA_NOTIFICATION_ICONS: dict[ArenaNotificationKind, str] = {
    ArenaNotificationKind.SUBMISSION_JUDGED: "bug_report",
    ArenaNotificationKind.AI_REVIEW_COMPLETED: "psychology_alt",
    ArenaNotificationKind.AI_REVIEW_FAILED: "cognition",
    ArenaNotificationKind.CLASS_REGISTRATION_REQUEST: "how_to_reg",
    ArenaNotificationKind.CLASS_REGISTRATION_APPROVED: "check_circle",
    ArenaNotificationKind.CLASS_REGISTRATION_DENIED: "cancel",
    ArenaNotificationKind.CLASS_MEMBERSHIP_ADDED: "person_add",
    ArenaNotificationKind.CLASS_MEMBERSHIP_REMOVED: "person_remove",
    ArenaNotificationKind.PROBLEM_REMOVAL_REQUEST: "delete_forever",
    ArenaNotificationKind.OTHER: "stacked_email",
}
