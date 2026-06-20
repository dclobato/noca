#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Prometheus metrics for the autojudge worker.

All metric objects are module-level singletons.  Import and update them
from any autojudge module.  This module imports nothing from autojudge.*
so it is a safe leaf with no circular-import risk.

The HTTP exposition server is started by run_worker() when
NOCA_JUDGE_METRICS_ENABLED is true.  Prometheus scrapes it at:
    GET http://<host>:<NOCA_JUDGE_METRICS_PORT>/metrics
"""

import asyncio
import logging

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Histogram bucket presets
# ---------------------------------------------------------------------------

_COMPILE_BUCKETS = (0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 180.0)
_RUN_TIME_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
_RUN_MEM_BUCKETS = (1024.0, 4096.0, 16384.0, 65536.0, 131072.0, 262144.0, 524288.0)
_POOL_ACQUIRE_BUCKETS = (0.01, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0)
_JOB_DURATION_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)

# ---------------------------------------------------------------------------
# Job processing
# ---------------------------------------------------------------------------

JOBS_DISPATCHED_TOTAL = Counter(
    "autojudge_jobs_dispatched_total",
    "Total jobs that acquired the idempotency lock and entered a pipeline.",
    ["job_kind"],
)

JOBS_COMPLETED_TOTAL = Counter(
    "autojudge_jobs_completed_total",
    "Total jobs that exited a pipeline, by outcome.",
    ["job_kind", "outcome"],
)

SUBMISSION_DURATION_SECONDS = Histogram(
    "autojudge_submission_duration_seconds",
    "Wall time from first test case start to verdict aggregation.",
    ["language_id"],
    buckets=_JOB_DURATION_BUCKETS,
)

PROFILING_DURATION_SECONDS = Histogram(
    "autojudge_profiling_duration_seconds",
    "Wall time for the full profiling test-case loop.",
    ["language_id"],
    buckets=_JOB_DURATION_BUCKETS,
)

VERDICTS_TOTAL = Counter(
    "autojudge_verdicts_total",
    "Total submission verdicts produced by the autojudge, by verdict and language.",
    ["verdict", "language_id"],
)

TEST_CASES_RUN_TOTAL = Counter(
    "autojudge_test_cases_run_total",
    "Total test cases executed across all submissions.",
    ["language_id"],
)

# ---------------------------------------------------------------------------
# Compile phase
# ---------------------------------------------------------------------------

COMPILE_TOTAL = Counter(
    "autojudge_compile_total",
    "Total compilation attempts by language and outcome.",
    ["language_id", "outcome"],
)

COMPILE_DURATION_SECONDS = Histogram(
    "autojudge_compile_duration_seconds",
    "Compile wall time in seconds, by language and outcome.",
    ["language_id", "outcome"],
    buckets=_COMPILE_BUCKETS,
)

# ---------------------------------------------------------------------------
# Isolate run phase
# ---------------------------------------------------------------------------

RUN_WALL_TIME_SECONDS = Histogram(
    "autojudge_run_wall_time_seconds",
    "Wall time reported by isolate per test case execution.",
    ["language_id"],
    buckets=_RUN_TIME_BUCKETS,
)

RUN_CPU_TIME_SECONDS = Histogram(
    "autojudge_run_cpu_time_seconds",
    "CPU time reported by isolate per test case execution.",
    ["language_id"],
    buckets=_RUN_TIME_BUCKETS,
)

RUN_MEMORY_KB = Histogram(
    "autojudge_run_memory_kb",
    "Peak memory (KB) reported by isolate per test case execution.",
    ["language_id"],
    buckets=_RUN_MEM_BUCKETS,
)

RUN_TIMEOUT_TOTAL = Counter(
    "autojudge_run_timeout_total",
    "Test case executions that timed out, by kind.",
    ["language_id", "timeout_kind"],
)

# ---------------------------------------------------------------------------
# Container pool
# ---------------------------------------------------------------------------

POOL_AVAILABLE_CONTAINERS = Gauge(
    "autojudge_pool_available_containers",
    "Current number of warm idle containers per language.",
    ["language_id"],
)

POOL_ACQUIRE_TOTAL = Counter(
    "autojudge_pool_acquire_total",
    "Total pool acquire attempts by language and outcome.",
    ["language_id", "outcome"],
)

POOL_ACQUIRE_DURATION_SECONDS = Histogram(
    "autojudge_pool_acquire_duration_seconds",
    "Time to acquire a container from the pool in seconds.",
    ["language_id", "outcome"],
    buckets=_POOL_ACQUIRE_BUCKETS,
)

CONTAINER_CREATE_TOTAL = Counter(
    "autojudge_container_create_total",
    "Total container creation attempts during pool replenishment, by outcome.",
    ["language_id", "outcome"],
)

# ---------------------------------------------------------------------------
# Queue depths  (polled, not event-driven)
# ---------------------------------------------------------------------------

QUEUE_DEPTH = Gauge(
    "autojudge_queue_depth",
    "Current depth of each Valkey judge queue.",
    ["queue"],
)

# ---------------------------------------------------------------------------
# Reaper
# ---------------------------------------------------------------------------

REAPER_CYCLES_TOTAL = Counter(
    "autojudge_reaper_cycles_total",
    "Total reaper scan cycles completed.",
)

REAPER_REQUEUED_TOTAL = Counter(
    "autojudge_reaper_requeued_total",
    "Total stale jobs requeued by the reaper.",
)

REAPER_DROPPED_TOTAL = Counter(
    "autojudge_reaper_dropped_total",
    "Total stale jobs dropped by the reaper (exceeded max requeue count).",
)

REAPER_ALREADY_DONE_TOTAL = Counter(
    "autojudge_reaper_already_done_total",
    "Total inflight entries the reaper found already completed.",
)

REAPER_ERRORS_TOTAL = Counter(
    "autojudge_reaper_errors_total",
    "Total exceptions caught during reaper cycles.",
)

# ---------------------------------------------------------------------------
# Worker process
# ---------------------------------------------------------------------------

WORKER_SLOTS_ACTIVE = Gauge(
    "autojudge_worker_slots_active",
    "Configured number of worker concurrency slots.",
)

WORKER_START_TIMESTAMP = Gauge(
    "autojudge_worker_start_timestamp_seconds",
    "Unix timestamp when the worker process started.",
)

# ---------------------------------------------------------------------------
# Gauge update helpers  (accept simple types — no autojudge.* imports needed)
# ---------------------------------------------------------------------------


def update_pool_gauges(pool_status: dict[str, int]) -> None:
    """
    Set POOL_AVAILABLE_CONTAINERS from pool_manager.pool_status() output.

    Args:
        pool_status: Mapping of language_id → available container count.
    """
    for lang_id, count in pool_status.items():
        POOL_AVAILABLE_CONTAINERS.labels(language_id=lang_id).set(count)


def update_queue_depths(depths: dict[str, int]) -> None:
    """
    Set QUEUE_DEPTH from a pre-computed queue-name → count mapping.

    Args:
        depths: Mapping of queue name → current list length.
    """
    for queue_name, count in depths.items():
        QUEUE_DEPTH.labels(queue=queue_name).set(count)


# ---------------------------------------------------------------------------
# Minimal asyncio HTTP server for /metrics exposition
# ---------------------------------------------------------------------------


async def _handle_metrics_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Serve one HTTP/1.x request; only responds to GET /metrics."""
    try:
        raw = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        request_line = raw.split(b"\r\n")[0].split(b"\n")[0].decode(errors="replace")
        parts = request_line.split()
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else ""
    except Exception:
        writer.close()
        return

    if path != "/metrics":
        response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
    elif method != "GET":
        response = b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n"
    else:
        body = generate_latest()
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: " + CONTENT_TYPE_LATEST.encode() + b"\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n" + body
        )

    try:
        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def start_metrics_server(port: int) -> asyncio.Server:
    """
    Start a minimal asyncio HTTP server that serves GET /metrics.

    Args:
        port: TCP port to bind on 0.0.0.0. Pass 0 to let the OS assign a free port.

    Returns:
        The running asyncio.Server; call server.close() during shutdown.
    """
    server = await asyncio.start_server(_handle_metrics_request, "0.0.0.0", port)
    actual_port = server.sockets[0].getsockname()[1]
    logger.info(f"Prometheus metrics available at http://0.0.0.0:{actual_port}/metrics")
    return server
