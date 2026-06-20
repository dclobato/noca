#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared Valkey queue constants."""

from __future__ import annotations

QUEUE_PENDING_KEY = "judge:queue:pending"
QUEUE_PRIORITY_KEY = "judge:queue:priority"
QUEUE_PROFILING_KEY = "judge:queue:profiling"
QUEUE_INFLIGHT_KEY = "judge:queue:inflight"
QUEUE_INFLIGHT_TIMES_KEY = "judge:queue:inflight:times"
QUEUE_RESULTS_CHANNEL = "judge:results"
# Arena verdicts are published on a dedicated channel so Arena live-feed subscribers
# never receive contest (web) verdict events and vice versa.
ARENA_RESULTS_CHANNEL = "arena:results"
QUEUE_JOB_HASH_PREFIX = "judge:job"
QUEUE_KEYS = (
    QUEUE_PRIORITY_KEY,
    QUEUE_PROFILING_KEY,
    QUEUE_PENDING_KEY,
    QUEUE_INFLIGHT_KEY,
)
QUEUE_UNKNOWN_CONTEST = "unknown_contest"

# AI review queue — separate namespace from the autojudge pipeline
QUEUE_AI_REVIEW_PENDING_KEY = "ai:queue:pending"
QUEUE_AI_REVIEW_INFLIGHT_KEY = "ai:queue:inflight"
QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY = "ai:queue:inflight:times"
QUEUE_AI_REVIEW_JOB_HASH_PREFIX = "ai:job"
AI_BATCH_TURNAROUND_STATS_KEY = "ai:batch:turnaround:stats"
