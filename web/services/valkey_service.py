#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Thin web-layer shim over the shared Valkey service package.

Creates a ValkeyRuntime using web application settings and re-exports all
symbols from the shared facade so existing imports continue to work.
"""

from shared.services.valkey_service import QUEUE_INFLIGHT_KEY as QUEUE_INFLIGHT_KEY
from shared.services.valkey_service import QUEUE_INFLIGHT_TIMES_KEY as QUEUE_INFLIGHT_TIMES_KEY
from shared.services.valkey_service import QUEUE_JOB_HASH_PREFIX as QUEUE_JOB_HASH_PREFIX
from shared.services.valkey_service import QUEUE_PENDING_KEY as QUEUE_PENDING_KEY
from shared.services.valkey_service import QUEUE_PRIORITY_KEY as QUEUE_PRIORITY_KEY
from shared.services.valkey_service import QUEUE_PROFILING_KEY as QUEUE_PROFILING_KEY
from shared.services.valkey_service import QUEUE_RESULTS_CHANNEL as QUEUE_RESULTS_CHANNEL
from shared.services.valkey_service import PendingCommand as PendingCommand
from shared.services.valkey_service import ValkeyRuntime as ValkeyRuntime
from shared.services.valkey_service import _dequeue_job_id_with_client as _dequeue_job_id_with_client
from shared.services.valkey_service import _enqueue_job_with_client as _enqueue_job_with_client
from shared.services.valkey_service import _enqueue_profiling_job_with_client as _enqueue_profiling_job_with_client
from shared.services.valkey_service import _publish_verdict_with_client as _publish_verdict_with_client
from shared.services.valkey_service import _remove_from_inflight_with_client as _remove_from_inflight_with_client
from shared.services.valkey_service import create_valkey_pool as create_valkey_pool
from shared.services.valkey_service import dequeue_job_id as dequeue_job_id
from shared.services.valkey_service import enqueue_job as enqueue_job
from shared.services.valkey_service import enqueue_profiling_job as enqueue_profiling_job
from shared.services.valkey_service import get_all_contest_queue_metrics as get_all_contest_queue_metrics
from shared.services.valkey_service import get_contest_queue_metrics as get_contest_queue_metrics
from shared.services.valkey_service import publish_verdict as publish_verdict
from shared.services.valkey_service import remove_from_inflight as remove_from_inflight
from web.config import settings as _settings


def create_web_valkey_runtime(*, healthcheck_interval_s: int) -> ValkeyRuntime:
    """Create a ValkeyRuntime configured from web application settings."""
    return ValkeyRuntime(
        valkey_url=_settings.valkey_url,
        healthcheck_interval_s=healthcheck_interval_s,
    )
