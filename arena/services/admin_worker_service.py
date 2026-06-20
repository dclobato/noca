#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena administration service for the worker-presence dashboard.

Pause/resume is built around PostgreSQL as the authoritative, monotonic source
of truth (``arena_worker_pause_state``). The route commits the pause-state bump
and an audit row *before* publishing a signed Valkey nudge, so a rolled-back
transaction can never leave a valid command pointing at non-existent state and a
failed publish never fails the operation — the worker reconciles from PG anyway.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.valkey_service import (
    ValkeyRuntime,
    WorkerClass,
    WorkerPresence,
    list_all_workers,
    remove_worker,
)
from shared.db_schema.arena import arena_ai_batch_jobs, arena_worker_command_audit
from shared.enumerations import ARENA_AI_BATCH_JOB_TERMINAL_STATUSES
from shared.services.valkey_service import (
    WorkerCommandType,
    build_command,
    publish_command,
)
from shared.services.worker_pause_state import bump_worker_pause_state, read_worker_pause_state

# Worker classes that expose pause/resume controls (rating is always-on).
PAUSABLE_CLASSES = (WorkerClass.AUTOJUDGE, WorkerClass.AIASSISTANT)

# Worker classes that expose one-shot trigger controls (flush/poll now).
TRIGGER_CLASSES = (WorkerClass.AIASSISTANT,)
_TRIGGER_ACTIONS = frozenset({WorkerCommandType.FLUSH_NOW, WorkerCommandType.POLL_NOW})

_COMMAND_TTL_SECONDS = 60

_CARD_METADATA = {
    WorkerClass.AUTOJUDGE: ("Autojudge workers", "gavel"),
    WorkerClass.RATING: ("Rating workers", "monitoring"),
    WorkerClass.AIASSISTANT: ("AI assistant workers", "smart_toy"),
}

_STATUS_METADATA = {
    WorkerClass.AUTOJUDGE: ("AutoJudge", "gavel"),
    WorkerClass.RATING: ("Rating", "monitoring"),
    WorkerClass.AIASSISTANT: ("AI Assistant", "smart_toy"),
}

_STATUS_CLASS_ORDER = (
    WorkerClass.AUTOJUDGE,
    WorkerClass.RATING,
    WorkerClass.AIASSISTANT,
)


@dataclass(frozen=True, slots=True)
class WorkerRow:
    """One dashboard worker row: presence plus its reconciled pause state."""

    worker_id: str
    online: bool
    started_at: datetime
    last_seen_at: datetime
    paused: bool
    paused_by: str | None
    last_job_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkerCard:
    """Presentation metadata and rows for one worker class."""

    worker_class: WorkerClass
    title: str
    icon: str
    workers: list[WorkerRow]
    supports_pause: bool
    pause_enabled: bool
    pending_tasks: int | None
    pending_batch_jobs: int | None = None
    supports_triggers: bool = False


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of a pause/resume request."""

    operation_succeeded: bool
    outcome: str
    transport_status: str
    generation: int | None


class WorkerAggregateState(StrEnum):
    """Aggregate availability state for one worker class."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class WorkerClassStatus:
    """User-facing aggregate status for one worker class."""

    worker_class: WorkerClass
    title: str
    icon: str
    state: WorkerAggregateState


async def list_worker_cards(
    session: AsyncSession,
    valkey_runtime: ValkeyRuntime,
    *,
    secret: str,
) -> list[WorkerCard]:
    """Return all worker classes in dashboard display order with pause state.

    Args:
        session: Database session for reading authoritative pause state.
        valkey_runtime: Valkey runtime for presence queries.
        secret: Worker-command secret; when empty, pause controls are disabled.

    Returns:
        A card per worker class with merged presence and pause state.
    """
    grouped_workers = await list_all_workers(valkey_runtime)
    pause_enabled = bool(secret)

    autojudge_queue_size = await valkey_runtime.get_autojudge_arena_queue_size()
    ai_queue_size = await valkey_runtime.get_ai_review_queue_size()
    queue_sizes: dict[WorkerClass, int | None] = {
        WorkerClass.AUTOJUDGE: autojudge_queue_size,
        WorkerClass.AIASSISTANT: ai_queue_size,
        WorkerClass.RATING: None,
    }

    ai_pending_batch_jobs = await _count_pending_batch_jobs(session)

    cards: list[WorkerCard] = []
    for worker_class in WorkerClass:
        supports_pause = worker_class in PAUSABLE_CLASSES
        supports_triggers = worker_class in TRIGGER_CLASSES
        rows: list[WorkerRow] = []
        for presence in grouped_workers[worker_class]:
            paused = False
            paused_by: str | None = None
            if supports_pause:
                state = await read_worker_pause_state(session, worker_class.value, presence.worker_id)
                if state is not None:
                    paused = state.paused
                    paused_by = state.paused_by
            rows.append(_to_row(presence, paused=paused, paused_by=paused_by))
        cards.append(
            WorkerCard(
                worker_class=worker_class,
                title=_CARD_METADATA[worker_class][0],
                icon=_CARD_METADATA[worker_class][1],
                workers=rows,
                supports_pause=supports_pause,
                pause_enabled=pause_enabled,
                pending_tasks=queue_sizes.get(worker_class),
                pending_batch_jobs=ai_pending_batch_jobs if worker_class is WorkerClass.AIASSISTANT else None,
                supports_triggers=supports_triggers,
            )
        )
    return cards


async def _count_pending_batch_jobs(session: AsyncSession) -> int:
    """Return the count of non-terminal AI batch jobs."""
    stmt = (
        select(func.count())
        .select_from(arena_ai_batch_jobs)
        .where(arena_ai_batch_jobs.c.local_status.not_in(ARENA_AI_BATCH_JOB_TERMINAL_STATUSES))
    )
    return (await session.execute(stmt)).scalar() or 0


def aggregate_worker_statuses(cards: list[WorkerCard]) -> list[WorkerClassStatus]:
    """Aggregate detailed worker cards into one availability state per class.

    Args:
        cards: Reconciled dashboard worker cards.

    Returns:
        One aggregate status per worker class in dashboard display order.
    """
    cards_by_class = {card.worker_class: card for card in cards}
    statuses: list[WorkerClassStatus] = []
    for worker_class in _STATUS_CLASS_ORDER:
        card = cards_by_class.get(worker_class)
        is_available = card is not None and any(worker.online and not worker.paused for worker in card.workers)
        statuses.append(
            _worker_class_status(
                worker_class,
                WorkerAggregateState.AVAILABLE if is_available else WorkerAggregateState.UNAVAILABLE,
            )
        )
    return statuses


def unknown_worker_statuses() -> list[WorkerClassStatus]:
    """Return unknown aggregate states for every worker class."""
    return [_worker_class_status(worker_class, WorkerAggregateState.UNKNOWN) for worker_class in _STATUS_CLASS_ORDER]


def _worker_class_status(
    worker_class: WorkerClass,
    state: WorkerAggregateState,
) -> WorkerClassStatus:
    """Build one aggregate worker-class status."""
    title, icon = _STATUS_METADATA[worker_class]
    return WorkerClassStatus(
        worker_class=worker_class,
        title=title,
        icon=icon,
        state=state,
    )


def _to_row(presence: WorkerPresence, *, paused: bool, paused_by: str | None) -> WorkerRow:
    """Flatten a presence record and its pause state into a dashboard row."""
    return WorkerRow(
        worker_id=presence.worker_id,
        online=presence.online,
        started_at=presence.started_at,
        last_seen_at=presence.last_seen_at,
        paused=paused,
        paused_by=paused_by,
        last_job_at=presence.last_job_at,
    )


async def remove_worker_from_dashboard(
    valkey_runtime: ValkeyRuntime,
    *,
    worker_class: WorkerClass,
    worker_id: str,
) -> None:
    """Remove a worker until its process publishes another heartbeat."""
    await remove_worker(
        valkey_runtime,
        worker_class=worker_class,
        worker_id=worker_id,
    )


async def pause_worker(
    session: AsyncSession,
    valkey_runtime: ValkeyRuntime,
    *,
    worker_class: WorkerClass | str,
    worker_id: str,
    paused_by: str,
    actor_user_id: str | None,
    secret: str,
) -> CommandResult:
    """Pause a worker: commit authoritative state and audit, then nudge."""
    return await _issue(
        session,
        valkey_runtime,
        action=WorkerCommandType.PAUSE,
        worker_class=worker_class,
        worker_id=worker_id,
        paused_by=paused_by,
        actor_user_id=actor_user_id,
        secret=secret,
    )


async def resume_worker(
    session: AsyncSession,
    valkey_runtime: ValkeyRuntime,
    *,
    worker_class: WorkerClass | str,
    worker_id: str,
    paused_by: str,
    actor_user_id: str | None,
    secret: str,
) -> CommandResult:
    """Resume a worker: commit authoritative state and audit, then nudge."""
    return await _issue(
        session,
        valkey_runtime,
        action=WorkerCommandType.RESUME,
        worker_class=worker_class,
        worker_id=worker_id,
        paused_by=paused_by,
        actor_user_id=actor_user_id,
        secret=secret,
    )


async def _issue(
    session: AsyncSession,
    valkey_runtime: ValkeyRuntime,
    *,
    action: WorkerCommandType,
    worker_class: WorkerClass | str,
    worker_id: str,
    paused_by: str,
    actor_user_id: str | None,
    secret: str,
) -> CommandResult:
    """Implement the strict issue ordering shared by pause and resume."""
    issued_at = datetime.now(UTC)

    # Step 1: validation — reject before any state change.
    raw_worker_class = worker_class.value if isinstance(worker_class, WorkerClass) else worker_class
    try:
        resolved_class = WorkerClass(raw_worker_class)
    except ValueError:
        await _audit(
            session,
            action=action,
            worker_class=raw_worker_class,
            worker_id=worker_id,
            actor_user_id=actor_user_id,
            actor_email=paused_by,
            generation=None,
            issued_at=issued_at,
            outcome="rejected_bad_request",
            transport_status="n_a",
        )
        await session.commit()
        return CommandResult(
            operation_succeeded=False,
            outcome="rejected_bad_request",
            transport_status="n_a",
            generation=None,
        )

    if resolved_class not in PAUSABLE_CLASSES:
        await _audit(
            session,
            action=action,
            worker_class=resolved_class.value,
            worker_id=worker_id,
            actor_user_id=actor_user_id,
            actor_email=paused_by,
            generation=None,
            issued_at=issued_at,
            outcome="rejected_bad_request",
            transport_status="n_a",
        )
        await session.commit()
        return CommandResult(
            operation_succeeded=False,
            outcome="rejected_bad_request",
            transport_status="n_a",
            generation=None,
        )

    if not secret:
        await _audit(
            session,
            action=action,
            worker_class=resolved_class.value,
            worker_id=worker_id,
            actor_user_id=actor_user_id,
            actor_email=paused_by,
            generation=None,
            issued_at=issued_at,
            outcome="rejected_disabled",
            transport_status="n_a",
        )
        await session.commit()
        return CommandResult(
            operation_succeeded=False,
            outcome="rejected_disabled",
            transport_status="n_a",
            generation=None,
        )

    # Step 2: commit authoritative state + audit in one transaction.
    paused = action is WorkerCommandType.PAUSE
    generation = await bump_worker_pause_state(
        session,
        worker_class=resolved_class.value,
        worker_id=worker_id,
        paused=paused,
        paused_by=paused_by,
    )
    await _audit(
        session,
        action=action,
        worker_class=resolved_class.value,
        worker_id=worker_id,
        actor_user_id=actor_user_id,
        actor_email=paused_by,
        generation=generation,
        issued_at=issued_at,
        outcome="committed",
        transport_status="pending",
    )
    await session.commit()

    # Step 3: only after commit, sign and publish the nudge.
    payload = build_command(
        secret,
        cmd=action,
        worker_class=resolved_class.value,
        worker_id=worker_id,
        generation=generation,
        paused_by=paused_by,
    )
    delivered = await publish_command(
        valkey_runtime,
        worker_class=resolved_class.value,
        worker_id=worker_id,
        payload=payload,
        ttl_seconds=_COMMAND_TTL_SECONDS,
    )

    # Step 4: record transport outcome (the operation already succeeded).
    transport_status = "delivered" if delivered else "transport_failed"
    await session.execute(
        arena_worker_command_audit.update()
        .where(
            arena_worker_command_audit.c.worker_class == resolved_class.value,
            arena_worker_command_audit.c.worker_id == worker_id,
            arena_worker_command_audit.c.generation == generation,
            arena_worker_command_audit.c.outcome == "committed",
        )
        .values(transport_status=transport_status)
    )
    await session.commit()

    return CommandResult(
        operation_succeeded=True,
        outcome="committed",
        transport_status=transport_status,
        generation=generation,
    )


async def trigger_worker(
    session: AsyncSession,
    valkey_runtime: ValkeyRuntime,
    *,
    worker_class: WorkerClass | str,
    worker_id: str,
    triggered_by: str,
    actor_user_id: str | None,
    action: WorkerCommandType,
    secret: str,
) -> CommandResult:
    """Send a one-shot trigger command (FLUSH_NOW or POLL_NOW) to a worker.

    Unlike pause/resume this does not touch ``arena_worker_pause_state``.
    An audit row is committed before the Valkey publish; its transport_status
    is updated afterwards.

    Args:
        session: Database session for audit writes.
        valkey_runtime: Valkey runtime for command publishing.
        worker_class: Target worker class (must be in ``TRIGGER_CLASSES``).
        worker_id: Target worker identifier.
        triggered_by: Email of the admin issuing the trigger.
        actor_user_id: Arena user id of the admin, for the audit row.
        action: Must be ``FLUSH_NOW`` or ``POLL_NOW``.
        secret: Shared signing secret; empty means disabled.

    Returns:
        ``CommandResult`` describing the outcome.
    """
    issued_at = datetime.now(UTC)
    raw_worker_class = worker_class.value if isinstance(worker_class, WorkerClass) else worker_class

    try:
        resolved_class = WorkerClass(raw_worker_class)
    except ValueError:
        await _audit(
            session,
            action=action,
            worker_class=raw_worker_class,
            worker_id=worker_id,
            actor_user_id=actor_user_id,
            actor_email=triggered_by,
            generation=None,
            issued_at=issued_at,
            outcome="rejected_bad_request",
            transport_status="n_a",
        )
        await session.commit()
        return CommandResult(
            operation_succeeded=False,
            outcome="rejected_bad_request",
            transport_status="n_a",
            generation=None,
        )

    if resolved_class not in TRIGGER_CLASSES or action not in _TRIGGER_ACTIONS:
        await _audit(
            session,
            action=action,
            worker_class=resolved_class.value,
            worker_id=worker_id,
            actor_user_id=actor_user_id,
            actor_email=triggered_by,
            generation=None,
            issued_at=issued_at,
            outcome="rejected_bad_request",
            transport_status="n_a",
        )
        await session.commit()
        return CommandResult(
            operation_succeeded=False,
            outcome="rejected_bad_request",
            transport_status="n_a",
            generation=None,
        )

    if not secret:
        await _audit(
            session,
            action=action,
            worker_class=resolved_class.value,
            worker_id=worker_id,
            actor_user_id=actor_user_id,
            actor_email=triggered_by,
            generation=None,
            issued_at=issued_at,
            outcome="rejected_disabled",
            transport_status="n_a",
        )
        await session.commit()
        return CommandResult(
            operation_succeeded=False,
            outcome="rejected_disabled",
            transport_status="n_a",
            generation=None,
        )

    # Commit audit row before publishing the nudge (trigger carries generation=0).
    audit_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_worker_command_audit).values(
            id=audit_id,
            actor_user_id=actor_user_id,
            actor_email=triggered_by,
            action=action.value,
            worker_class=resolved_class.value,
            worker_id=worker_id,
            generation=None,
            issued_at=issued_at,
            outcome="triggered",
            transport_status="pending",
        )
    )
    await session.commit()

    payload = build_command(
        secret,
        cmd=action,
        worker_class=resolved_class.value,
        worker_id=worker_id,
        generation=0,
        paused_by=triggered_by,
    )
    delivered = await publish_command(
        valkey_runtime,
        worker_class=resolved_class.value,
        worker_id=worker_id,
        payload=payload,
        ttl_seconds=_COMMAND_TTL_SECONDS,
    )

    transport_status = "delivered" if delivered else "transport_failed"
    await session.execute(
        arena_worker_command_audit.update()
        .where(arena_worker_command_audit.c.id == audit_id)
        .values(transport_status=transport_status)
    )
    await session.commit()

    return CommandResult(
        operation_succeeded=True,
        outcome="triggered",
        transport_status=transport_status,
        generation=None,
    )


async def _audit(
    session: AsyncSession,
    *,
    action: WorkerCommandType,
    worker_class: str,
    worker_id: str,
    actor_user_id: str | None,
    actor_email: str | None,
    generation: int | None,
    issued_at: datetime,
    outcome: str,
    transport_status: str,
) -> None:
    """Insert one command-audit row in the caller's transaction."""
    await session.execute(
        insert(arena_worker_command_audit).values(
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            action=action.value,
            worker_class=worker_class,
            worker_id=worker_id,
            generation=generation,
            issued_at=issued_at,
            outcome=outcome,
            transport_status=transport_status,
        )
    )
