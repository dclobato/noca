#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Valkey runtime lifecycle and buffering."""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import json
import logging
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, cast

import valkey.asyncio as aivalkey
from valkey.asyncio.connection import ConnectionPool

from shared.queue_schema import (
    AllContestQueueMetrics,
    ArenaAIReviewJob,
    ArenaSubmissionJob,
    ArenaVerdictEvent,
    ContestQueueMetrics,
    JudgeJob,
    ProfilingJob,
    VerdictEvent,
)
from shared.services.valkey_service.constants import ARENA_RESULTS_CHANNEL, QUEUE_RESULTS_CHANNEL
from shared.services.valkey_service.errors import is_recoverable_valkey_error
from shared.services.valkey_service.pool import create_valkey_pool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PendingCommand:
    """Buffered Valkey write to replay after reconnect."""

    operation: Literal[
        "enqueue_job",
        "enqueue_profiling_job",
        "enqueue_arena_submission_job",
        "enqueue_arena_ai_review_job",
        "complete_arena_ai_review_job",
        "remove_from_inflight",
        "remove_from_ai_review_inflight",
        "publish_verdict",
    ]
    job: JudgeJob | ProfilingJob | ArenaSubmissionJob | ArenaAIReviewJob | None = None
    priority: bool = False
    job_id: str | None = None
    event: VerdictEvent | None = None


class ValkeyRuntime:
    """Valkey pool/client owner with health checks and pending command replay."""

    def __init__(self, *, valkey_url: str, healthcheck_interval_s: int) -> None:
        self._valkey_url = valkey_url
        self._healthcheck_interval_s = max(1, healthcheck_interval_s)
        self._pool: ConnectionPool | None = None
        self._client: aivalkey.Valkey | None = None
        self._is_available = False
        self._is_started = False

        self._stop_event = asyncio.Event()
        self._health_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[bool] | None = None
        self._reconnect_lock = asyncio.Lock()
        self._pending_lock = asyncio.Lock()
        self._pending_commands: deque[PendingCommand] = deque()

    @property
    def is_available(self) -> bool:
        """Whether the runtime currently considers Valkey reachable."""
        return self._is_available

    @property
    def pending_count(self) -> int:
        """Number of buffered write commands waiting for replay."""
        return len(self._pending_commands)

    async def start(self) -> None:
        """Connect and start the periodic health loop."""
        connected = await self._connect_new_client(raise_on_failure=True)
        if not connected:
            raise RuntimeError("Failed to initialize Valkey runtime")
        self._is_started = True
        self._stop_event.clear()
        self._health_task = asyncio.create_task(
            self._health_loop(),
            name="valkey-healthcheck",
        )

    async def stop(self) -> None:
        """Stop background tasks and close any owned Valkey resources."""
        self._stop_event.set()
        self._is_started = False

        health_task = self._health_task
        self._health_task = None
        if health_task is not None:
            await health_task

        reconnect_task = self._reconnect_task
        self._reconnect_task = None
        if reconnect_task is not None:
            await reconnect_task

        client = self._client
        pool = self._pool
        self._client = None
        self._pool = None
        self._is_available = False

        if client is not None:
            await client.aclose()
        if pool is not None:
            await pool.aclose()

    async def get(self, key: str) -> str | None:
        """Fetch a string value by key. Returns None on miss or recoverable error."""
        client = self._client
        if client is None:
            return None
        try:
            result = await client.get(key)
            return str(result) if result is not None else None
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey key '{key}' get failed: {str(exc)}")
                return None
            raise

    async def get_and_delete(self, key: str) -> str | None:
        """Atomically fetch and delete a string key.

        Returns:
            The removed value, or None on a miss or recoverable error.
        """
        client = self._client
        if client is None:
            return None
        try:
            result = await client.getdel(key)
            return str(result) if result is not None else None
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey key '{key}' getdel failed: {str(exc)}")
                return None
            raise

    async def mget(self, keys: list[str]) -> list[str | None] | None:
        """Fetch multiple string values by key. Returns None on recoverable error."""
        client = self._client
        if client is None:
            return None
        try:
            values = await client.mget(keys)
            return [str(value) if value is not None else None for value in values]
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey mget failed. Key_count: {len(keys)}: {str(exc)}")
                return None
            raise

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        """Set a string value. Best-effort; recoverable errors are swallowed."""
        client = self._client
        if client is None:
            return
        try:
            await client.set(key, value, ex=ex)
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey key '{key}' set failed: {str(exc)}")
                return
            raise

    async def set_reporting(self, key: str, value: str, *, ex: int) -> bool:
        """Atomically set a key with expiry, reporting delivery success.

        Unlike :meth:`set`, the boolean return lets callers audit whether the
        write reached Valkey. Returns ``False`` on a recoverable error (so a
        failed nudge can be recorded without aborting an already-committed
        operation) and re-raises unrecoverable errors.
        """
        client = self._client
        if client is None:
            return False
        try:
            await client.set(key, value, ex=ex)
            return True
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey key '{key}' set_reporting failed: {str(exc)}")
                return False
            raise

    async def set_if_absent(self, key: str, value: str, *, ex: int | None = None) -> bool | None:
        """Set a string value only when absent. Returns None on recoverable error."""
        client = self._client
        if client is None:
            return None
        try:
            result = await client.set(key, value, ex=ex, nx=True)
            return result is True
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey key '{key}' set_if_absent failed: {str(exc)}")
                return None
            raise

    async def delete(self, *keys: str) -> None:
        """Delete one or more keys. Best-effort; recoverable errors are swallowed."""
        client = self._client
        if client is None:
            return
        try:
            await client.delete(*keys)
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey key '{keys}' delete failed: {str(exc)}")
                return
            raise

    async def hset(self, key: str, field: str, value: str) -> None:
        """Set one hash field. Best-effort; recoverable errors are swallowed."""
        client = self._client
        if client is None:
            return
        try:
            result = client.hset(key, field, value)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey hset '{key}' field='{field}' failed: {str(exc)}")
                return
            raise

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        """Fetch multiple hash fields. Returns list of None on recoverable error."""
        client = self._client
        if client is None:
            return [None] * len(fields)
        try:
            raw = client.hmget(key, fields)
            values: list[Any] = cast(list[Any], await raw) if asyncio.iscoroutine(raw) else cast(list[Any], raw)
            return [str(v) if v is not None else None for v in values]
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey hmget '{key}' failed: {str(exc)}")
                return [None] * len(fields)
            raise

    async def hdel(self, key: str, *fields: str) -> None:
        """Delete one or more hash fields. Best-effort; recoverable errors are swallowed."""
        client = self._client
        if client is None:
            return
        try:
            result = client.hdel(key, *fields)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey hdel '{key}' fields={fields} failed: {str(exc)}")
                return
            raise

    async def hgetall(self, key: str) -> dict[str, str] | None:
        """Fetch all fields from a hash. Returns None on recoverable error."""
        client = self._client
        if client is None:
            return None
        try:
            result = client.hgetall(key)
            if asyncio.iscoroutine(result):
                result = await result
            values = cast(dict[Any, Any], result)
            return {str(field): str(value) for field, value in values.items()}
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey hgetall '{key}' failed: {str(exc)}")
                return None
            raise

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Execute a Valkey script. Returns None on recoverable error."""
        client = self._client
        if client is None:
            return None
        try:
            result = client.eval(script, numkeys, *args)
            if asyncio.iscoroutine(result):
                return cast(object, await result)
            return cast(object, result)
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning("Valkey eval failed")
                logger.warning(json.dumps({"numkeys": numkeys, "arg_count": len(args), "error": str(exc)}, indent=2))
                return None
            raise

    async def enqueue_job(self, job: JudgeJob, *, priority: bool) -> None:
        """Execute or buffer an enqueue-job command."""
        command = PendingCommand(operation="enqueue_job", job=job, priority=priority)
        await self._execute_or_buffer(command)

    async def enqueue_profiling_job(self, job: ProfilingJob) -> None:
        """Execute or buffer a profiling enqueue command."""
        command = PendingCommand(operation="enqueue_profiling_job", job=job)
        await self._execute_or_buffer(command)

    async def enqueue_arena_submission_job(self, job: ArenaSubmissionJob) -> None:
        """Execute or buffer an Arena submission enqueue command."""
        command = PendingCommand(operation="enqueue_arena_submission_job", job=job)
        await self._execute_or_buffer(command)

    async def enqueue_arena_ai_review_job(self, job: ArenaAIReviewJob) -> None:
        """Execute or buffer an Arena AI review enqueue command."""
        command = PendingCommand(operation="enqueue_arena_ai_review_job", job=job)
        await self._execute_or_buffer(command)

    async def dequeue_arena_ai_review_job_id(self) -> str | None:
        """Dequeue the next submission_id from the AI review pending queue."""
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            self._is_available = False
            self._schedule_reconnect()
            return None

        try:
            job_id = valkey_facade._dequeue_arena_ai_review_job_id_with_client(client)
            if asyncio.iscoroutine(job_id):
                return await job_id
            return job_id
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                self._is_available = False
                self._schedule_reconnect()
                logger.warning(f"Valkey AI review dequeue failed; reconnect scheduled: {str(exc)}")
                return None
            raise

    async def remove_from_ai_review_inflight(self, submission_id: str) -> None:
        """Execute or buffer a remove-from-AI-review-inflight command."""
        command = PendingCommand(operation="remove_from_ai_review_inflight", job_id=submission_id)
        try:
            await self._execute_or_buffer(command)
        except Exception as exc:
            logger.error(f"Failed to remove AI review job '{submission_id}' from inflight list: {str(exc)}")

    async def complete_arena_ai_review_job(self, submission_id: str) -> None:
        """Execute or buffer terminal cleanup for an Arena AI review job."""
        command = PendingCommand(operation="complete_arena_ai_review_job", job_id=submission_id)
        try:
            await self._execute_or_buffer(command)
        except Exception as exc:
            logger.error(f"Failed to complete AI review job '{submission_id}': {str(exc)}")

    async def get_stale_ai_review_job_ids(self, stale_threshold_s: float) -> list[str]:
        """Return submission_ids that have been inflight longer than stale_threshold_s seconds.

        Args:
            stale_threshold_s: Age in seconds after which a job is considered stale.

        Returns:
            List of submission_id strings. Empty on Valkey unavailability or error.
        """
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            return []
        try:
            return await valkey_facade._get_stale_ai_review_job_ids_with_client(client, stale_threshold_s)
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey stale AI review job scan failed: {str(exc)}")
                return []
            raise

    async def get_ai_review_job_hash(self, submission_id: str) -> dict[str, str] | None:
        """Return the job metadata hash for the given submission_id, or None.

        Args:
            submission_id: Submission UUID whose hash to retrieve.

        Returns:
            Dict of string fields, or None when unavailable or not found.
        """
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            return None
        try:
            return await valkey_facade._get_ai_review_job_hash_with_client(client, submission_id)
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey AI review job hash read failed for {submission_id}: {str(exc)}")
                return None
            raise

    async def get_ai_review_queued_ids(self) -> builtins.set[str]:
        """Return submission_ids currently in the AI review pending or inflight queues.

        Returns:
            Set of submission_id strings. Empty on Valkey unavailability or error.
        """
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            return set()
        try:
            return await valkey_facade._get_ai_review_queued_ids_with_client(client)
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                logger.warning(f"Valkey AI review queued-id scan failed: {str(exc)}")
                return set()
            raise

    async def dequeue_job_id(self) -> str | None:
        """Dequeue the next available job id."""
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            self._is_available = False
            self._schedule_reconnect()
            return None

        try:
            job_id = valkey_facade._dequeue_job_id_with_client(client)
            if asyncio.iscoroutine(job_id):
                return await job_id
            return job_id
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                self._is_available = False
                self._schedule_reconnect()
                logger.warning(f"Valkey dequeue failed; reconnect scheduled: {str(exc)}")
                return None
            raise

    async def get_contest_queue_metrics(self, contest_id: str) -> ContestQueueMetrics | None:
        """Return queue metrics for one contest. Returns None on recoverable error."""
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            self._is_available = False
            self._schedule_reconnect()
            return None

        try:
            metrics = await valkey_facade._get_contest_queue_metrics_with_client(client, contest_id)
            self._is_available = True
            return metrics
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                self._is_available = False
                self._schedule_reconnect()
                logger.warning(
                    f"Valkey contest '{contest_id}' queue metrics read failed; reconnect scheduled: {str(exc)}"
                )
                return None
            raise

    async def get_all_contest_queue_metrics(self) -> AllContestQueueMetrics | None:
        """Return per-contest queue aggregation for all contests."""
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            self._is_available = False
            self._schedule_reconnect()
            return None

        try:
            metrics = await valkey_facade._get_all_contest_queue_metrics_with_client(client)
            self._is_available = True
            return metrics
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                self._is_available = False
                self._schedule_reconnect()
                logger.warning(f"Valkey all-contest queue metrics read failed; reconnect scheduled: {str(exc)}")
                return None
            raise

    async def get_autojudge_arena_queue_size(self) -> int | None:
        """Count autojudge queue entries with job_kind == arena_submission. None on error."""
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            self._is_available = False
            self._schedule_reconnect()
            return None

        try:
            count = await valkey_facade._get_autojudge_arena_queue_size_with_client(client)
            self._is_available = True
            return count
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                self._is_available = False
                self._schedule_reconnect()
                logger.warning(f"Valkey autojudge arena queue size read failed: {str(exc)}")
                return None
            raise

    async def get_ai_review_queue_size(self) -> int | None:
        """Return total AI review jobs in pending + inflight queues. None on error."""
        from shared.services import valkey_service as valkey_facade

        client = self._client
        if client is None:
            self._is_available = False
            self._schedule_reconnect()
            return None

        try:
            count = await valkey_facade._get_ai_review_queue_size_with_client(client)
            self._is_available = True
            return count
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                self._is_available = False
                self._schedule_reconnect()
                logger.warning(f"Valkey AI review queue size read failed: {str(exc)}")
                return None
            raise

    async def remove_from_inflight(self, job_id: str) -> None:
        """Execute or buffer a remove-from-inflight command."""
        command = PendingCommand(operation="remove_from_inflight", job_id=job_id)
        try:
            await self._execute_or_buffer(command)
        except Exception as exc:
            logger.error(f"Failed to remove job '{job_id}' from inflight list: {str(exc)}")

    async def publish_verdict(self, event: VerdictEvent) -> None:
        """Execute or buffer a verdict-publish command."""
        command = PendingCommand(operation="publish_verdict", event=event)
        try:
            await self._execute_or_buffer(command)
        except Exception as exc:
            logger.error("Failed to publish verdict event")
            logger.error(
                json.dumps(
                    {
                        "submission_id": event.submission_id,
                        "verdict": event.verdict,
                        "error": str(exc),
                    },
                    indent=2,
                )
            )

    async def iter_verdict_events(self) -> AsyncIterator[VerdictEvent]:
        """Yield verdict events from the Valkey pub/sub channel until interrupted."""
        if self._client is None:
            return

        client: aivalkey.Valkey = aivalkey.Valkey.from_url(
            self._valkey_url,
            decode_responses=True,
            socket_keepalive=True,
            socket_timeout=None,
        )
        pubsub = client.pubsub()
        await pubsub.subscribe(QUEUE_RESULTS_CHANNEL)
        try:
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            yield VerdictEvent.model_validate_json(message["data"])
                        except Exception as exc:
                            logger.warning("Failed to parse VerdictEvent from pub/sub: %s", exc)
            except Exception as exc:
                if is_recoverable_valkey_error(exc):
                    logger.debug("Verdict pub/sub stream interrupted; caller may reconnect: %s", exc)
                    return
                raise
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(QUEUE_RESULTS_CHANNEL)
            await client.aclose()

    async def iter_arena_verdict_events(self) -> AsyncIterator[ArenaVerdictEvent]:
        """Yield Arena verdict events from the Valkey pub/sub channel until interrupted.

        This is a subscriber only. Arena verdicts are published exclusively by the
        autojudge worker through the raw-client helper
        ``publish_arena_verdict_with_client`` (see ``autojudge/queue_ops.py``). Nothing
        publishes Arena events through ``ValkeyRuntime``, so there is deliberately no
        ``publish_arena_verdict`` here and no ``PendingCommand`` operation for it. A future
        Arena HTTP-side publisher would need to add: a ``PendingCommand`` operation, an
        ``event`` field type, the replay branch in ``_execute_pending_command``, and a
        ``publish_arena_verdict`` method (mirroring ``publish_verdict``).
        """
        if self._client is None:
            return

        client: aivalkey.Valkey = aivalkey.Valkey.from_url(
            self._valkey_url,
            decode_responses=True,
            socket_keepalive=True,
            socket_timeout=None,
        )
        pubsub = client.pubsub()
        await pubsub.subscribe(ARENA_RESULTS_CHANNEL)
        try:
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            yield ArenaVerdictEvent.model_validate_json(message["data"])
                        except Exception as exc:
                            logger.warning("Failed to parse ArenaVerdictEvent from pub/sub: %s", exc)
            except Exception as exc:
                if is_recoverable_valkey_error(exc):
                    logger.debug("Arena verdict pub/sub stream interrupted; caller may reconnect: %s", exc)
                    return
                raise
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(ARENA_RESULTS_CHANNEL)
            await client.aclose()

    async def _health_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._run_health_iteration()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._healthcheck_interval_s)
            except TimeoutError:
                continue

    async def _run_health_iteration(self) -> None:
        client = self._client
        if client is None:
            self._is_available = False
            self._schedule_reconnect()
            return
        try:
            await client.ping()
            self._is_available = True
            if self.pending_count > 0:
                async with self._reconnect_lock:
                    await self._flush_pending_commands_locked()
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                self._is_available = False
                self._schedule_reconnect()
                logger.warning(f"Valkey health check failed; reconnect scheduled: {str(exc)}")
                return
            raise

    async def _execute_or_buffer(self, command: PendingCommand) -> None:
        client = self._client
        if client is None:
            await self._push_pending(command)
            self._is_available = False
            self._schedule_reconnect()
            return

        try:
            await self._execute_pending_command(client, command)
            self._is_available = True
        except Exception as exc:
            if is_recoverable_valkey_error(exc):
                self._is_available = False
                await self._push_pending(command)
                self._schedule_reconnect()
                logger.warning("Valkey write failed; command buffered for replay")
                logger.warning(
                    json.dumps(
                        {"operation": command.operation, "error": str(exc), "pending_count": self.pending_count},
                        indent=2,
                    )
                )
                return
            raise

    async def _connect_new_client(self, *, raise_on_failure: bool = False) -> bool:
        async with self._reconnect_lock:
            new_pool = create_valkey_pool(self._valkey_url)
            new_client = aivalkey.Valkey.from_pool(new_pool)

            try:
                await new_client.ping()
            except Exception as exc:
                await new_client.aclose()
                await new_pool.aclose()
                self._is_available = False
                if raise_on_failure:
                    raise
                logger.warning(f"Valkey reconnect attempt failed: {str(exc)}")
                return False

            old_client = self._client
            old_pool = self._pool
            self._client = new_client
            self._pool = new_pool
            self._is_available = True

            if old_client is not None:
                await old_client.aclose()
            if old_pool is not None:
                await old_pool.aclose()

            await self._flush_pending_commands_locked()
            return True

    def _schedule_reconnect(self) -> None:
        if self._stop_event.is_set() or not self._is_started:
            return

        reconnect_task = self._reconnect_task
        if reconnect_task is not None and not reconnect_task.done():
            return

        self._reconnect_task = asyncio.create_task(
            self._connect_new_client(),
            name="valkey-reconnect",
        )

    async def _push_pending(self, command: PendingCommand) -> None:
        async with self._pending_lock:
            self._pending_commands.append(command)

    async def _push_pending_left(self, command: PendingCommand) -> None:
        async with self._pending_lock:
            self._pending_commands.appendleft(command)

    async def _pop_pending(self) -> PendingCommand | None:
        async with self._pending_lock:
            if not self._pending_commands:
                return None
            return self._pending_commands.popleft()

    async def _flush_pending_commands_locked(self) -> None:
        client = self._client
        if client is None:
            return

        flushed = 0
        while True:
            command = await self._pop_pending()
            if command is None:
                break
            try:
                await self._execute_pending_command(client, command)
                flushed += 1
            except Exception as exc:
                if is_recoverable_valkey_error(exc):
                    await self._push_pending_left(command)
                    self._is_available = False
                    logger.warning("Valkey failed while flushing pending commands")
                    logger.warning(json.dumps({"error": str(exc), "pending_count": self.pending_count}, indent=2))
                    return
                logger.error("Dropping pending Valkey command due unrecoverable failure")
                logger.error(json.dumps({"operation": command.operation, "error": str(exc)}, indent=2))

        if flushed > 0:
            logger.info(f"Flushed '{flushed}' buffered Valkey commands")

    async def _execute_pending_command(self, client: aivalkey.Valkey, command: PendingCommand) -> None:
        from shared.services import valkey_service as valkey_facade

        if command.operation == "enqueue_job":
            if command.job is None:
                raise RuntimeError("enqueue_job pending command without job payload")
            await valkey_facade._enqueue_job_with_client(client, cast(JudgeJob, command.job), priority=command.priority)
            return

        if command.operation == "enqueue_profiling_job":
            if command.job is None:
                raise RuntimeError("enqueue_profiling_job pending command without job payload")
            await valkey_facade._enqueue_profiling_job_with_client(client, cast(ProfilingJob, command.job))
            return

        if command.operation == "enqueue_arena_submission_job":
            if command.job is None:
                raise RuntimeError("enqueue_arena_submission_job pending command without job payload")
            await valkey_facade._enqueue_arena_submission_job_with_client(
                client,
                cast(ArenaSubmissionJob, command.job),
            )
            return

        if command.operation == "enqueue_arena_ai_review_job":
            if command.job is None:
                raise RuntimeError("enqueue_arena_ai_review_job pending command without job payload")
            await valkey_facade._enqueue_arena_ai_review_job_with_client(
                client,
                cast(ArenaAIReviewJob, command.job),
            )
            return

        if command.operation == "remove_from_inflight":
            if command.job_id is None:
                raise RuntimeError("remove_from_inflight pending command without job_id")
            await valkey_facade._remove_from_inflight_with_client(client, command.job_id)
            return

        if command.operation == "remove_from_ai_review_inflight":
            if command.job_id is None:
                raise RuntimeError("remove_from_ai_review_inflight pending command without job_id")
            await valkey_facade._remove_from_ai_review_inflight_with_client(client, command.job_id)
            return

        if command.operation == "complete_arena_ai_review_job":
            if command.job_id is None:
                raise RuntimeError("complete_arena_ai_review_job pending command without job_id")
            await valkey_facade._complete_arena_ai_review_job_with_client(client, command.job_id)
            return

        if command.operation == "publish_verdict":
            if command.event is None:
                raise RuntimeError("publish_verdict pending command without event payload")
            await valkey_facade._publish_verdict_with_client(client, command.event)
            return

        raise RuntimeError(f"Unknown pending command operation: {command.operation}")
