#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Thin Arena-layer shim over the shared Valkey service package.

Creates a ValkeyRuntime using Arena application settings and re-exports shared
queue helpers so Arena code can depend on its own module boundary.
"""

from arena.config import settings as _settings
from shared.services.valkey_service import QUEUE_INFLIGHT_KEY as QUEUE_INFLIGHT_KEY
from shared.services.valkey_service import QUEUE_INFLIGHT_TIMES_KEY as QUEUE_INFLIGHT_TIMES_KEY
from shared.services.valkey_service import QUEUE_JOB_HASH_PREFIX as QUEUE_JOB_HASH_PREFIX
from shared.services.valkey_service import QUEUE_PENDING_KEY as QUEUE_PENDING_KEY
from shared.services.valkey_service import QUEUE_PRIORITY_KEY as QUEUE_PRIORITY_KEY
from shared.services.valkey_service import QUEUE_PROFILING_KEY as QUEUE_PROFILING_KEY
from shared.services.valkey_service import QUEUE_RESULTS_CHANNEL as QUEUE_RESULTS_CHANNEL
from shared.services.valkey_service import PendingCommand as PendingCommand
from shared.services.valkey_service import ValkeyRuntime as ValkeyRuntime
from shared.services.valkey_service import WorkerClass as WorkerClass
from shared.services.valkey_service import WorkerPresence as WorkerPresence
from shared.services.valkey_service import _dequeue_job_id_with_client as _dequeue_job_id_with_client
from shared.services.valkey_service import (
    _enqueue_arena_submission_job_with_client as _enqueue_arena_submission_job_with_client,
)
from shared.services.valkey_service import _enqueue_job_with_client as _enqueue_job_with_client
from shared.services.valkey_service import _enqueue_profiling_job_with_client as _enqueue_profiling_job_with_client
from shared.services.valkey_service import _publish_verdict_with_client as _publish_verdict_with_client
from shared.services.valkey_service import _remove_from_inflight_with_client as _remove_from_inflight_with_client
from shared.services.valkey_service import create_valkey_pool as create_valkey_pool
from shared.services.valkey_service import dequeue_job_id as dequeue_job_id
from shared.services.valkey_service import enqueue_arena_submission_job as enqueue_arena_submission_job
from shared.services.valkey_service import enqueue_job as enqueue_job
from shared.services.valkey_service import enqueue_profiling_job as enqueue_profiling_job
from shared.services.valkey_service import get_all_contest_queue_metrics as get_all_contest_queue_metrics
from shared.services.valkey_service import get_contest_queue_metrics as get_contest_queue_metrics
from shared.services.valkey_service import list_all_workers as list_all_workers
from shared.services.valkey_service import publish_verdict as publish_verdict
from shared.services.valkey_service import remove_from_inflight as remove_from_inflight
from shared.services.valkey_service import remove_worker as remove_worker


def create_arena_valkey_runtime(*, healthcheck_interval_s: int) -> ValkeyRuntime:
    """Create a ValkeyRuntime configured from Arena application settings.

    Args:
        healthcheck_interval_s: Interval between Valkey health checks.

    Returns:
        ValkeyRuntime: Configured runtime instance.
    """
    return ValkeyRuntime(
        valkey_url=_settings.valkey_url,
        healthcheck_interval_s=healthcheck_interval_s,
    )
