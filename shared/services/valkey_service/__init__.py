#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared Valkey service facade with a stable import surface."""

from __future__ import annotations

from shared.services.valkey_service.constants import (
    AI_BATCH_TURNAROUND_STATS_KEY,
    ARENA_RESULTS_CHANNEL,
    QUEUE_AI_REVIEW_INFLIGHT_KEY,
    QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY,
    QUEUE_AI_REVIEW_JOB_HASH_PREFIX,
    QUEUE_AI_REVIEW_PENDING_KEY,
    QUEUE_INFLIGHT_KEY,
    QUEUE_INFLIGHT_TIMES_KEY,
    QUEUE_JOB_HASH_PREFIX,
    QUEUE_KEYS,
    QUEUE_PENDING_KEY,
    QUEUE_PRIORITY_KEY,
    QUEUE_PROFILING_KEY,
    QUEUE_RESULTS_CHANNEL,
    QUEUE_UNKNOWN_CONTEST,
)
from shared.services.valkey_service.pool import create_valkey_pool
from shared.services.valkey_service.queue_metrics import (
    contest_id_from_job_hash as _contest_id_from_job_hash,
)
from shared.services.valkey_service.queue_metrics import (
    get_ai_review_queue_size_with_client as _get_ai_review_queue_size_with_client,
)
from shared.services.valkey_service.queue_metrics import (
    get_all_contest_queue_metrics_with_client as _get_all_contest_queue_metrics_with_client,
)
from shared.services.valkey_service.queue_metrics import (
    get_autojudge_arena_queue_size_with_client as _get_autojudge_arena_queue_size_with_client,
)
from shared.services.valkey_service.queue_metrics import (
    get_contest_queue_metrics_with_client as _get_contest_queue_metrics_with_client,
)
from shared.services.valkey_service.queue_metrics import (
    read_job_hashes_for_ids_with_client as _read_job_hashes_for_ids_with_client,
)
from shared.services.valkey_service.queue_metrics import (
    read_queue_ids_with_client as _read_queue_ids_with_client,
)
from shared.services.valkey_service.queue_ops import (
    complete_arena_ai_review_job,
    dequeue_arena_ai_review_job_id,
    dequeue_job_id,
    enqueue_arena_ai_review_job,
    enqueue_arena_submission_job,
    enqueue_job,
    enqueue_profiling_job,
    get_ai_review_job_hash,
    get_ai_review_queued_ids,
    get_all_contest_queue_metrics,
    get_contest_queue_metrics,
    get_stale_ai_review_job_ids,
    publish_verdict,
    remove_from_ai_review_inflight,
    remove_from_inflight,
)
from shared.services.valkey_service.queue_ops import (
    complete_arena_ai_review_job_with_client as _complete_arena_ai_review_job_with_client,
)
from shared.services.valkey_service.queue_ops import (
    dequeue_arena_ai_review_job_id_with_client as _dequeue_arena_ai_review_job_id_with_client,
)
from shared.services.valkey_service.queue_ops import (
    dequeue_job_id_with_client as _dequeue_job_id_with_client,
)
from shared.services.valkey_service.queue_ops import (
    enqueue_arena_ai_review_job_with_client as _enqueue_arena_ai_review_job_with_client,
)
from shared.services.valkey_service.queue_ops import (
    enqueue_arena_submission_job_with_client as _enqueue_arena_submission_job_with_client,
)
from shared.services.valkey_service.queue_ops import (
    enqueue_job_with_client as _enqueue_job_with_client,
)
from shared.services.valkey_service.queue_ops import (
    enqueue_profiling_job_with_client as _enqueue_profiling_job_with_client,
)
from shared.services.valkey_service.queue_ops import (
    get_ai_review_job_hash_with_client as _get_ai_review_job_hash_with_client,
)
from shared.services.valkey_service.queue_ops import (
    get_ai_review_queued_ids_with_client as _get_ai_review_queued_ids_with_client,
)
from shared.services.valkey_service.queue_ops import (
    get_stale_ai_review_job_ids_with_client as _get_stale_ai_review_job_ids_with_client,
)
from shared.services.valkey_service.queue_ops import (
    publish_arena_verdict_with_client as _publish_arena_verdict_with_client,
)
from shared.services.valkey_service.queue_ops import (
    publish_verdict_with_client as _publish_verdict_with_client,
)
from shared.services.valkey_service.queue_ops import (
    remove_from_ai_review_inflight_with_client as _remove_from_ai_review_inflight_with_client,
)
from shared.services.valkey_service.queue_ops import (
    remove_from_inflight_with_client as _remove_from_inflight_with_client,
)
from shared.services.valkey_service.runtime import PendingCommand, ValkeyRuntime
from shared.services.valkey_service.worker_commands import (
    CommandVerdict,
    LivePauseFlag,
    WorkerCommandType,
    build_command,
    claim_nonce,
    publish_command,
    reconcile_worker_pause_state,
    verify_command,
    worker_command_key,
    worker_command_loop,
    worker_command_nonce_key,
)
from shared.services.valkey_service.worker_presence import (
    WORKER_PRESENCE_PREFIX,
    WorkerClass,
    WorkerPresence,
    list_all_workers,
    list_workers,
    mark_worker_offline,
    publish_worker_last_job,
    publish_worker_presence,
    remove_worker,
    resolve_worker_id,
    worker_last_jobs_key,
    worker_live_key,
    worker_presence_loop,
    worker_registry_key,
)

__all__ = [
    "AI_BATCH_TURNAROUND_STATS_KEY",
    "ARENA_RESULTS_CHANNEL",
    "CommandVerdict",
    "LivePauseFlag",
    "PendingCommand",
    "WorkerCommandType",
    "QUEUE_AI_REVIEW_INFLIGHT_KEY",
    "QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY",
    "QUEUE_AI_REVIEW_JOB_HASH_PREFIX",
    "QUEUE_AI_REVIEW_PENDING_KEY",
    "QUEUE_INFLIGHT_KEY",
    "QUEUE_INFLIGHT_TIMES_KEY",
    "QUEUE_JOB_HASH_PREFIX",
    "QUEUE_KEYS",
    "QUEUE_PENDING_KEY",
    "QUEUE_PRIORITY_KEY",
    "QUEUE_PROFILING_KEY",
    "QUEUE_RESULTS_CHANNEL",
    "QUEUE_UNKNOWN_CONTEST",
    "ValkeyRuntime",
    "WORKER_PRESENCE_PREFIX",
    "WorkerClass",
    "WorkerPresence",
    "_contest_id_from_job_hash",
    "_complete_arena_ai_review_job_with_client",
    "_dequeue_arena_ai_review_job_id_with_client",
    "_dequeue_job_id_with_client",
    "_enqueue_arena_ai_review_job_with_client",
    "_enqueue_arena_submission_job_with_client",
    "_enqueue_job_with_client",
    "_enqueue_profiling_job_with_client",
    "_get_ai_review_job_hash_with_client",
    "_get_ai_review_queued_ids_with_client",
    "_get_ai_review_queue_size_with_client",
    "_get_all_contest_queue_metrics_with_client",
    "_get_autojudge_arena_queue_size_with_client",
    "_get_contest_queue_metrics_with_client",
    "_get_stale_ai_review_job_ids_with_client",
    "_publish_arena_verdict_with_client",
    "_publish_verdict_with_client",
    "_read_job_hashes_for_ids_with_client",
    "_read_queue_ids_with_client",
    "_remove_from_ai_review_inflight_with_client",
    "_remove_from_inflight_with_client",
    "build_command",
    "claim_nonce",
    "complete_arena_ai_review_job",
    "create_valkey_pool",
    "dequeue_arena_ai_review_job_id",
    "dequeue_job_id",
    "enqueue_arena_ai_review_job",
    "enqueue_arena_submission_job",
    "enqueue_job",
    "enqueue_profiling_job",
    "get_ai_review_job_hash",
    "get_ai_review_queued_ids",
    "get_all_contest_queue_metrics",
    "get_contest_queue_metrics",
    "get_stale_ai_review_job_ids",
    "list_all_workers",
    "list_workers",
    "mark_worker_offline",
    "publish_command",
    "publish_verdict",
    "publish_worker_last_job",
    "publish_worker_presence",
    "reconcile_worker_pause_state",
    "remove_from_ai_review_inflight",
    "remove_from_inflight",
    "remove_worker",
    "resolve_worker_id",
    "verify_command",
    "worker_command_key",
    "worker_command_loop",
    "worker_command_nonce_key",
    "worker_last_jobs_key",
    "worker_live_key",
    "worker_presence_loop",
    "worker_registry_key",
]
