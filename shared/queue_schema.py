#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
shared/queue_schema.py

Pydantic models representing the data structures exchanged through Valkey.
The job queue carries lightweight judgment references only; the worker
loads the full submission payload from PostgreSQL.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class JobKind(str):
    """Valkey hash discriminator for worker job dispatch."""

    SUBMISSION = "submission"
    ARENA_SUBMISSION = "arena_submission"
    PROFILING = "profiling"
    ARENA_AI_REVIEW = "arena_ai_review"


class JudgeJob(BaseModel):
    """
    Lightweight payload stored in Valkey at judge:job:<judgment_id>.

    Queue list items remain the raw `judgment_id` string for minimal overhead.
    The per-job hash stores queue metadata used by reaper/observability
    tooling, while the worker still loads authoritative submission/problem
    payload from PostgreSQL after dequeue.

    During rollout transitions, legacy hashes may not have `contest_id`.
    Consumers that inspect hashes must treat missing `contest_id` as unknown
    and continue safely.
    """

    judgment_id: str = Field(description="UUID of the submission_judgment record")
    contest_id: str = Field(description="UUID of the contest that owns this judgment")
    submission_id: str | None = Field(default=None, description="Optional submission UUID for traceability")
    is_rejudge: bool = Field(default=False)
    requeue_count: int = Field(default=0, description="Times this job has been requeued by the judger reaper")
    job_kind: str = Field(default=JobKind.SUBMISSION, description="Worker dispatch discriminator")

    model_config = {"frozen": True}  # jobs are immutable once enqueued


class ProfilingJob(BaseModel):
    """Lightweight payload stored in Valkey for problem profiling runs."""

    profiling_run_id: str
    contest_id: str
    problem_id: str
    language_id: str
    requeue_count: int = Field(default=0, description="Times this job has been requeued by the judger reaper")
    job_kind: str = Field(default=JobKind.PROFILING, description="Worker dispatch discriminator")

    model_config = {"frozen": True}


class ArenaSubmissionJob(BaseModel):
    """Lightweight payload stored in Valkey for Arena submission judgments."""

    judgment_id: str = Field(description="UUID of the arena_submission_judgments record")
    submission_id: str = Field(description="UUID of the arena_submissions record")
    user_id: str = Field(description="UUID of the Arena user that owns the submission")
    problem_id: str = Field(description="UUID of the Arena problem being judged")
    language_id: str = Field(description="Language key used to compile and run the submission")
    requeue_count: int = Field(default=0, description="Times this job has been requeued by the judger reaper")
    job_kind: str = Field(default=JobKind.ARENA_SUBMISSION, description="Worker dispatch discriminator")

    model_config = {"frozen": True}


class ArenaAIReviewJob(BaseModel):
    """Lightweight payload stored in Valkey for Arena AI code-review requests.

    Queue list items carry the raw ``submission_id`` string. The per-job hash
    stores queue metadata (requeue_count, job_kind) used by the reaper and
    observability tooling; the worker loads the full source code and problem
    statement from PostgreSQL after dequeue.

    Recovery semantics mirror ArenaSubmissionJob: if the worker crashes while
    the item is inflight, the reaper will increment requeue_count and push the
    submission_id back onto the pending queue.  Items that exceed
    REAPER_MAX_REQUEUE_COUNT are dropped as poison pills.
    """

    submission_id: str = Field(description="UUID of the arena_submissions record to review")
    user_id: str = Field(description="UUID of the Arena user that owns the submission")
    problem_id: str = Field(description="UUID of the Arena problem being reviewed")
    language_id: str = Field(description="Language key used in the submission (e.g. 'gcc-c17')")
    use_platform_key: bool = Field(
        default=False,
        description="Frozen at request time: True when the platform API key should be used",
    )
    requeue_count: int = Field(default=0, description="Times this job has been requeued by the reaper")
    job_kind: str = Field(default=JobKind.ARENA_AI_REVIEW, description="Worker dispatch discriminator")

    model_config = {"frozen": True}


class AIBatchTurnaroundStats(BaseModel):
    """Recent platform-key AI review turnaround statistics stored in Valkey."""

    version: Literal[1] = 1
    average_seconds: float = Field(ge=0)
    median_seconds: float = Field(ge=0)
    stddev_seconds: float = Field(ge=0)
    sample_count: int = Field(ge=1, le=100)
    updated_at: datetime

    model_config = {"frozen": True}


class VerdictEvent(BaseModel):
    """
    Published to Valkey judge:results after a judgment is finalized.

    Only final verdicts are published; autojudge outcomes that still require
    human review must not be published via this event.

    Fields
    ------
    update_kind : str | None
        Why this event was published. One of ``"autojudge"``, ``"confirmation"``,
        or ``"override"``.
    team_id : str | None
        The team that owns the submission. Populated by all publishers so the
        SSE layer can include it in live-visibility payloads.
    problem_id : str | None
        The problem being judged. Populated by all publishers for the same
        reason.
    """

    submission_id: str
    judgment_id: str
    verdict: str  # Verdict enum value
    contest_id: str | None = None
    compile_log: str | None = None
    score: float | None = None
    judge_time_ms: int | None = None
    update_kind: Literal["autojudge", "confirmation", "override"] | None = None
    team_id: str | None = None
    problem_id: str | None = None

    model_config = {"frozen": True}


class ArenaVerdictEvent(BaseModel):
    """
    Published to Valkey ``arena:results`` after an Arena judgment is finalized.

    Arena has no human-review step, so every finalized Arena verdict is published.
    The payload is intentionally minimal: the Arena live feed uses it only as a
    "something changed" signal and refetches an authoritative snapshot from the
    Arena HTTP process, so no display fields are carried here.
    """

    submission_id: str
    judgment_id: str
    verdict: str  # Verdict enum value

    model_config = {"frozen": True}


class ContestQueueMetrics(BaseModel):
    """Queue counters for one contest derived from Valkey only.

    All counts reflect queue-membership at read time. If the same judgment_id
    appears in more than one queue (unusual but possible during transitions),
    it is counted once per queue it occupies.

    `unknown_contest_seen` counts queue items whose job hash had no `contest_id`
    (legacy hashes written before the field was added). It is incremented once
    per queue-membership of a legacy item, mirroring the per-queue-item counting
    used for the per-contest counts.
    """

    contest_id: str
    profiling_count: int = 0
    priority_count: int = 0
    pending_count: int = 0
    inflight_count: int = 0
    total_count: int = 0
    unknown_contest_seen: int = 0
    scanned_queue_items: int = 0
    metrics_latency_ms: float = 0.0

    model_config = {"frozen": True}


class AllContestQueueMetrics(BaseModel):
    """Multi-contest queue aggregation derived from Valkey only.

    Provides per-contest and per-queue-per-contest counts across all active
    queue items. Intended for ops tooling and monitoring dashboards.

    `unknown_contest_items` counts queue items with no `contest_id` in their
    job hash (legacy hashes from before the field was added). These are
    bucketed separately and do not appear in per-contest counts.

    Dict fields reflect queue state at read time and are not frozen because
    their values are mutable mapping types.
    """

    by_queue_and_contest: dict[str, dict[str, int]]
    by_contest_total: dict[str, int]
    total_items: int = 0
    unknown_contest_items: int = 0
    missing_ratio: float = 0.0
    metrics_latency_ms: float = 0.0
