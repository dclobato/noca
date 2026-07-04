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


TERMINAL_JUDGMENT_STATUSES: frozenset[JudgmentStatus] = frozenset(
    {JudgmentStatus.DONE, JudgmentStatus.FAILED, JudgmentStatus.SUPERSEDED}
)
"""Judgment statuses that are terminal. ``FAILED`` and ``SUPERSEDED`` are final without a verdict."""


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
    ArenaNotificationKind.TEACHER_FEEDBACK_POSTED: "rate_review",
    ArenaNotificationKind.OTHER: "stacked_email",
}


class ArenaBadge(StrEnum):
    """Gamification badges awardable to Arena users.

    Each value equals the basename of the corresponding asset in
    arena/static/img/badges/<value>.png.
    """

    HELLO_WORLD = "helloworld"
    ONE_SHOT = "oneshot"
    FULL_CLEAR = "fullclear"
    BUG_KILLER = "bugkiller"
    CLEAN_CODE = "cleancode"
    BIT_SCRUBBER = "bitscrubber"
    NIGHT_WORKER = "nightworker"
    WEEKEND_WORKER = "weekendworker"
    STRIKE_3 = "strike3"
    STRIKE_7 = "strike7"
    STRIKE_30 = "strike30"
    NEVER_GIVE_UP = "nevergiveup"
    PROBLEMS_10 = "10problems"
    PROBLEMS_25 = "25problems"
    PROBLEMS_100 = "100problems"
    PROBLEMS_500 = "500problems"
    FIRST_TO_HAND_IN = "firsttohandin"
    FIRST_SOLVER = "firstsolver"
    LANGUAGES_3 = "3languages"
    LANGUAGES_5 = "5languages"
    LANGUAGES_10 = "10languages"
    LOCO_CODER = "lococoder"
    THIS_IS_THE_WAY = "thisistheway"
    ROCK_CRACKER = "rockcracker"
    ALMOST_LATE = "almostlate"
    TRIMMER = "trimmer"


# Display metadata for each badge. "label" is a short human title, "description" is the
# achievement criterion shown to users, and "image" is the asset basename under
# arena/static/img/badges/. There is no DB column for this; award logic and UI read it.
ARENA_BADGE_METADATA: dict[str, dict[str, str]] = {
    ArenaBadge.HELLO_WORLD.value: {
        "label": "Hello, World!",
        "description": "Solve at least one problem",
        "image": "helloworld.png",
    },
    ArenaBadge.ONE_SHOT.value: {
        "label": "One Shot",
        "description": "Solve a problem on first attempt",
        "image": "oneshot.png",
    },
    ArenaBadge.FULL_CLEAR.value: {
        "label": "Full Clear",
        "description": "Solved all problems on a problem set",
        "image": "fullclear.png",
    },
    ArenaBadge.BUG_KILLER.value: {
        "label": "Bug Killer",
        "description": "Fixed a runtime error on next submission",
        "image": "bugkiller.png",
    },
    ArenaBadge.CLEAN_CODE.value: {
        "label": "Clean Code",
        "description": "Solution for a problem in the top 5% by execution time or memory",
        "image": "cleancode.png",
    },
    ArenaBadge.BIT_SCRUBBER.value: {
        "label": "Bit Scrubber",
        "description": "Fixed a time or memory limit error on next submission",
        "image": "bitscrubber.png",
    },
    ArenaBadge.NIGHT_WORKER.value: {
        "label": "Night Worker",
        "description": "Solved a problem late at night",
        "image": "nightworker.png",
    },
    ArenaBadge.WEEKEND_WORKER.value: {
        "label": "Weekend Worker",
        "description": "Solved a problem on weekend",
        "image": "weekendworker.png",
    },
    ArenaBadge.STRIKE_3.value: {
        "label": "Strike 3",
        "description": "Solved problems 3 days on a row",
        "image": "strike3.png",
    },
    ArenaBadge.STRIKE_7.value: {
        "label": "Strike 7",
        "description": "Solved problems 7 days on a row",
        "image": "strike7.png",
    },
    ArenaBadge.STRIKE_30.value: {
        "label": "Strike 30",
        "description": "Solved problems 30 days on a row",
        "image": "strike30.png",
    },
    ArenaBadge.NEVER_GIVE_UP.value: {
        "label": "Never Give Up",
        "description": "Solved a problem after 5 wrong answer",
        "image": "nevergiveup.png",
    },
    ArenaBadge.PROBLEMS_10.value: {
        "label": "10 Problems",
        "description": "Solved 10 different problems",
        "image": "10problems.png",
    },
    ArenaBadge.PROBLEMS_25.value: {
        "label": "25 Problems",
        "description": "Solved 25 different problems",
        "image": "25problems.png",
    },
    ArenaBadge.PROBLEMS_100.value: {
        "label": "100 Problems",
        "description": "Solved 100 different problems",
        "image": "100problems.png",
    },
    ArenaBadge.PROBLEMS_500.value: {
        "label": "500 Problems",
        "description": "Solved 500 different problems",
        "image": "500problems.png",
    },
    ArenaBadge.FIRST_TO_HAND_IN.value: {
        "label": "First to Hand In",
        "description": "Be the first to solve a problem through a problem set",
        "image": "firsttohandin.png",
    },
    ArenaBadge.FIRST_SOLVER.value: {
        "label": "First Solver",
        "description": "Be the first user to solve a problem",
        "image": "firstsolver.png",
    },
    ArenaBadge.LANGUAGES_3.value: {
        "label": "3 Languages",
        "description": "Solve the same problem in 3 different languages",
        "image": "3languages.png",
    },
    ArenaBadge.LANGUAGES_5.value: {
        "label": "5 Languages",
        "description": "Solve the same problem in 5 different languages",
        "image": "5languages.png",
    },
    ArenaBadge.LANGUAGES_10.value: {
        "label": "10 Languages",
        "description": "Solve the same problem in 10 different languages",
        "image": "10languages.png",
    },
    ArenaBadge.LOCO_CODER.value: {
        "label": "Loco Coder",
        "description": "Get 3 non-AC verdicts on the same problem within 90 seconds",
        "image": "lococoder.png",
    },
    ArenaBadge.THIS_IS_THE_WAY.value: {
        "label": "This Is the Way",
        "description": "Solve 15 different problems in a row without a non-AC submission",
        "image": "thisistheway.png",
    },
    ArenaBadge.ROCK_CRACKER.value: {
        "label": "Rock Cracker",
        "description": "Solve a problem with a solve rate below 20%",
        "image": "rockcracker.png",
    },
    ArenaBadge.ALMOST_LATE.value: {
        "label": "Almost Late",
        "description": "Be the last on-time solver for a problem in a problem set",
        "image": "almostlate.png",
    },
    ArenaBadge.TRIMMER.value: {
        "label": "Trimmer",
        "description": "Fix a presentation error on the next submission",
        "image": "trimmer.png",
    },
}
