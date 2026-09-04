#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared Valkey queue constants."""

from __future__ import annotations

import os

_TEST_CHANNEL_NAMESPACE = os.environ.get("NOCA_TEST_VALKEY_CHANNEL_NAMESPACE", "").strip(":")


def _channel_name(name: str) -> str:
    """Return a production channel name or its test-isolated equivalent."""
    if not _TEST_CHANNEL_NAMESPACE:
        return name
    return f"{_TEST_CHANNEL_NAMESPACE}:{name}"


QUEUE_PENDING_KEY = "judge:queue:pending"
QUEUE_PRIORITY_KEY = "judge:queue:priority"
QUEUE_PROFILING_KEY = "judge:queue:profiling"
QUEUE_INFLIGHT_KEY = "judge:queue:inflight"
QUEUE_INFLIGHT_TIMES_KEY = "judge:queue:inflight:times"
QUEUE_RESULTS_CHANNEL = _channel_name("judge:results")
# New-submission nudges are published on their own channel so animator
# subscribers can react to submissions without touching the verdict channel.
QUEUE_SUBMISSIONS_CHANNEL = _channel_name("judge:submissions")
# Arena verdicts are published on a dedicated channel so Arena live-feed subscribers
# never receive contest (web) verdict events and vice versa.
ARENA_RESULTS_CHANNEL = _channel_name("arena:results")
QUEUE_JOB_HASH_PREFIX = "judge:job"
QUEUE_KEYS = (
    QUEUE_PRIORITY_KEY,
    QUEUE_PROFILING_KEY,
    QUEUE_PENDING_KEY,
    QUEUE_INFLIGHT_KEY,
)
QUEUE_UNKNOWN_CONTEST = "unknown_contest"

# Reveal ceremony persistence and projection pub/sub (animator).
# Keys and channels are built from *validated* contest and scope components by
# shared/services/valkey_service/revelation.py; never format these prefixes with
# raw, unvalidated strings.
REVEAL_STATE_KEY_PREFIX = "animator:reveal"
REVEAL_LOCK_KEY_PREFIX = "animator:reveal:lock"
REVEAL_CONTROLLER_KEY_PREFIX = "animator:reveal:controller"
REVEAL_PROJECTORS_KEY_PREFIX = "animator:reveal:projectors"
REVELATION_CHANNEL_PREFIX = _channel_name("revelation:events")

# AI review queue — separate namespace from the autojudge pipeline
QUEUE_AI_REVIEW_PENDING_KEY = "ai:queue:pending"
QUEUE_AI_REVIEW_INFLIGHT_KEY = "ai:queue:inflight"
QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY = "ai:queue:inflight:times"
QUEUE_AI_REVIEW_JOB_HASH_PREFIX = "ai:job"
AI_BATCH_TURNAROUND_STATS_KEY = "ai:batch:turnaround:stats"

# Outbound email queue drained by the mailer worker — its own namespace so a
# flood of mail never competes with judging or AI review keys
QUEUE_MAIL_PENDING_KEY = "mail:queue:pending"
QUEUE_MAIL_INFLIGHT_KEY = "mail:queue:inflight"
QUEUE_MAIL_INFLIGHT_TIMES_KEY = "mail:queue:inflight:times"
QUEUE_MAIL_JOB_HASH_PREFIX = "mail:job"
