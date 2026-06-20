#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Signed pause/resume command transport for Arena queue-consuming workers.

This module carries an authenticated *nudge*, not authoritative state. The
durable, monotonic source of truth is PostgreSQL (``arena_worker_pause_state``).
A worker derives its paused/running state only from committed rows there; a
verified command merely triggers an immediate re-read. Consequently a replayed,
forged, or raced command can never advance state on its own.

Trust model:

- Commands are signed with a shared secret (``HMAC-SHA256``); without the secret
  the feature is disabled and nothing is published or accepted.
- Verification binds each command to its target worker class/id, enforces
  symmetric freshness (rejecting both stale and future-dated timestamps), and a
  single-use nonce guards against replay within the freshness window.
- Transport is a single atomic per-worker ``SET … EX`` key; there are no list
  operations and therefore no partial-write window.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import math
import secrets
import string
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from shared.services.worker_pause_state import read_worker_pause_state

WORKER_COMMAND_PREFIX = "noca:worker-command"

_SIGNED_FIELDS = ("cmd", "worker_class", "worker_id", "generation", "paused_by", "issued_at", "nonce")


class WorkerCommandType(StrEnum):
    """Advisory action carried by a worker command (logged, not trusted)."""

    PAUSE = "pause"
    RESUME = "resume"
    FLUSH_NOW = "flush_now"
    POLL_NOW = "poll_now"


_COMMAND_VALUES = frozenset(command.value for command in WorkerCommandType)
_TRIGGER_CMDS = frozenset({WorkerCommandType.FLUSH_NOW.value, WorkerCommandType.POLL_NOW.value})


@dataclass(frozen=True, slots=True)
class CommandVerdict:
    """Outcome of verifying a received command payload."""

    ok: bool
    reason: str | None
    cmd: str | None = None
    generation: int | None = None
    nonce: str | None = None
    paused_by: str | None = None


@dataclass(slots=True)
class LivePauseFlag:
    """Run-scoped mutable view of a worker's reconciled pause state."""

    paused: bool = False
    paused_by: str | None = None
    applied_generation: int = 0


def worker_command_key(worker_class: str, worker_id: str) -> str:
    """Return the per-worker command transport key."""
    return f"{WORKER_COMMAND_PREFIX}:{worker_class}:{worker_id}"


def worker_command_nonce_key(nonce: str) -> str:
    """Return the single-use replay-guard key for a nonce."""
    return f"{WORKER_COMMAND_PREFIX}:nonce:{nonce}"


def _canonical(payload: dict[str, Any]) -> str:
    """Return the canonical signing string over every field except ``sig``."""
    canonical = {field: payload[field] for field in _SIGNED_FIELDS}
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def _sign(secret: str, canonical: str) -> str:
    """Return the hex HMAC-SHA256 signature of ``canonical`` under ``secret``."""
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def build_command(
    secret: str,
    *,
    cmd: WorkerCommandType,
    worker_class: str,
    worker_id: str,
    generation: int,
    paused_by: str | None,
) -> dict[str, Any]:
    """Build and sign a command payload bound to one worker and generation.

    Args:
        secret: Shared signing secret (must be non-empty).
        cmd: Advisory pause/resume action.
        worker_class: Target worker class.
        worker_id: Target worker identifier.
        generation: Committed pause-state generation this command refers to.
        paused_by: Email of the issuing admin.

    Returns:
        A JSON-serializable dict including the ``sig`` field.

    Raises:
        ValueError: If ``secret`` is empty.
    """
    if not secret:
        raise ValueError("Worker command secret must not be empty.")

    payload: dict[str, Any] = {
        "cmd": cmd.value,
        "worker_class": worker_class,
        "worker_id": worker_id,
        "generation": generation,
        "paused_by": paused_by,
        "issued_at": time.time(),
        "nonce": secrets.token_hex(16),
    }
    payload["sig"] = _sign(secret, _canonical(payload))
    return payload


async def publish_command(
    client: Any,
    *,
    worker_class: str,
    worker_id: str,
    payload: dict[str, Any],
    ttl_seconds: int,
) -> bool:
    """Publish a signed command atomically; return delivery success for auditing.

    Args:
        client: ValkeyRuntime exposing ``set_reporting``.
        worker_class: Target worker class.
        worker_id: Target worker identifier.
        payload: Signed command dict.
        ttl_seconds: Expiry for the transport key.

    Returns:
        True when the command was written, False on recoverable transport error.
    """
    key = worker_command_key(worker_class, worker_id)
    value = json.dumps(payload, separators=(",", ":"))
    return bool(await client.set_reporting(key, value, ex=ttl_seconds))


def verify_command(
    secret: str,
    payload: dict[str, Any],
    *,
    expected_class: str,
    expected_id: str,
    now: float,
    freshness_seconds: float,
) -> CommandVerdict:
    """Verify a received command's signature, target binding, and freshness.

    Args:
        secret: Shared signing secret; empty disables the feature.
        payload: Parsed command dict.
        expected_class: Receiving worker's own class.
        expected_id: Receiving worker's own identifier.
        now: Current epoch seconds.
        freshness_seconds: Allowed absolute clock skew window.

    Returns:
        A ``CommandVerdict`` with ``ok`` and a failure ``reason`` when invalid.
    """
    if not secret:
        return CommandVerdict(ok=False, reason="disabled")

    if not isinstance(payload, dict) or set(payload) != {*_SIGNED_FIELDS, "sig"}:
        return CommandVerdict(ok=False, reason="malformed")

    cmd = payload["cmd"]
    worker_class = payload["worker_class"]
    worker_id = payload["worker_id"]
    generation = payload["generation"]
    paused_by = payload["paused_by"]
    issued_at = payload["issued_at"]
    nonce = payload["nonce"]
    provided_sig = payload["sig"]
    if (
        not isinstance(cmd, str)
        or cmd not in _COMMAND_VALUES
        or not isinstance(worker_class, str)
        or not worker_class
        or not isinstance(worker_id, str)
        or not worker_id
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
        or (paused_by is not None and (not isinstance(paused_by, str) or not paused_by))
        or not isinstance(issued_at, int | float)
        or isinstance(issued_at, bool)
        or not math.isfinite(float(issued_at))
        or not isinstance(nonce, str)
        or len(nonce) != 32
        or any(character not in string.hexdigits for character in nonce)
        or not isinstance(provided_sig, str)
    ):
        return CommandVerdict(ok=False, reason="malformed")

    try:
        expected_sig = _sign(secret, _canonical(payload))
    except TypeError, KeyError:
        return CommandVerdict(ok=False, reason="malformed")

    if not hmac.compare_digest(expected_sig, provided_sig):
        return CommandVerdict(ok=False, reason="bad_signature")

    if worker_class != expected_class or worker_id != expected_id:
        return CommandVerdict(ok=False, reason="wrong_target")

    if abs(now - float(issued_at)) > freshness_seconds:
        return CommandVerdict(ok=False, reason="expired")

    return CommandVerdict(
        ok=True,
        reason=None,
        cmd=cmd,
        generation=generation,
        nonce=nonce,
        paused_by=paused_by,
    )


async def claim_nonce(client: Any, nonce: str, *, ttl_seconds: int) -> bool:
    """Claim a nonce exactly once; fail closed on any ambiguity.

    Returns True only when the key was newly set. A prior claim, a Valkey error,
    or an unavailable client all return False so a command is never applied twice.

    Args:
        client: ValkeyRuntime (``set_if_absent``) or raw Valkey client (``set``).
        nonce: Command nonce to claim.
        ttl_seconds: Expiry for the nonce key.

    Returns:
        True only when this call won the claim.
    """
    key = worker_command_nonce_key(nonce)
    set_if_absent = getattr(client, "set_if_absent", None)
    try:
        if set_if_absent is not None:
            result = await set_if_absent(key, "1", ex=ttl_seconds)
            return result is True
        result = await client.set(key, "1", nx=True, ex=ttl_seconds)
    except Exception:
        return False
    return result is True


async def _client_get_and_delete(client: Any, key: str) -> str | None:
    """Atomically consume a string value across runtime and raw Valkey clients."""
    try:
        get_and_delete = getattr(client, "get_and_delete", None)
        if get_and_delete is not None:
            result = await get_and_delete(key)
        else:
            result = await client.getdel(key)
    except Exception:
        return None
    if isinstance(result, bytes):
        return result.decode("utf-8")
    return result if result is None else str(result)


async def reconcile_worker_pause_state(
    session_factory: Callable[[], AbstractAsyncContextManager[Any]],
    *,
    worker_class: str,
    worker_id: str,
    flag: LivePauseFlag,
    logger: logging.Logger,
    expected_generation: int | None = None,
) -> bool:
    """Adopt a newer committed pause state, optionally matching one generation.

    Args:
        session_factory: Callable yielding an async DB session or connection.
        worker_class: Target worker class.
        worker_id: Target worker identifier.
        flag: Run-scoped pause state to update.
        logger: Logger for reconcile failures and applied state.
        expected_generation: When set, require PostgreSQL to contain exactly
            this generation before applying state.

    Returns:
        True when the live flag adopted a newer committed generation.
    """
    try:
        async with session_factory() as session:
            pg = await read_worker_pause_state(session, worker_class, worker_id)
    except Exception as exc:
        logger.warning("Worker pause-state reconcile read failed: %s", exc)
        return False

    if pg is None:
        return False
    if expected_generation is not None and pg.generation != expected_generation:
        return False
    if pg.generation <= flag.applied_generation:
        return False

    flag.paused = pg.paused
    flag.paused_by = pg.paused_by
    flag.applied_generation = pg.generation
    logger.info(
        "Reconciled pause state: paused=%s by=%s generation=%s",
        pg.paused,
        pg.paused_by,
        pg.generation,
    )
    return True


async def worker_command_loop(
    client: Any,
    session_factory: Callable[[], AbstractAsyncContextManager[Any]],
    *,
    worker_class: str,
    worker_id: str,
    secret: str,
    poll_seconds: float,
    freshness_seconds: float,
    nonce_ttl_seconds: int,
    flag: LivePauseFlag,
    stop_event: asyncio.Event,
    logger: logging.Logger,
    reconcile_seconds: float = 60.0,
    on_trigger: Callable[[str], None] | None = None,
) -> None:
    """Poll for commands and periodically reconcile authoritative pause state.

    Args:
        client: Valkey client for the command/nonce keys.
        session_factory: Callable yielding an async DB session/connection.
        worker_class: This worker's class.
        worker_id: This worker's identifier.
        secret: Shared signing secret (empty disables verification).
        poll_seconds: Reconcile/poll cadence.
        freshness_seconds: Symmetric command freshness window.
        nonce_ttl_seconds: Replay-guard nonce TTL (should exceed freshness).
        flag: Run-scoped pause flag mutated in place.
        stop_event: Set to request shutdown.
        logger: Loop logger.
        reconcile_seconds: Fallback PostgreSQL reconciliation interval.
        on_trigger: Optional callback invoked with the command value when a
            one-shot trigger command (FLUSH_NOW / POLL_NOW) is received and
            verified. When ``None``, trigger commands are claimed and silently
            ignored (autojudge path).
    """
    next_reconcile_at = time.monotonic() + reconcile_seconds

    while not stop_event.is_set():
        # Step 1: atomically consume and verify the fast-path nudge.
        raw = await _client_get_and_delete(client, worker_command_key(worker_class, worker_id))
        if raw is not None:
            await _handle_command(
                client,
                raw,
                session_factory,
                worker_class=worker_class,
                worker_id=worker_id,
                secret=secret,
                freshness_seconds=freshness_seconds,
                nonce_ttl_seconds=nonce_ttl_seconds,
                flag=flag,
                logger=logger,
                on_trigger=on_trigger,
            )

        # Step 2: PG remains authoritative, with a slower fallback for lost nudges.
        if time.monotonic() >= next_reconcile_at:
            await reconcile_worker_pause_state(
                session_factory,
                worker_class=worker_class,
                worker_id=worker_id,
                flag=flag,
                logger=logger,
            )
            next_reconcile_at = time.monotonic() + reconcile_seconds

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)


async def _handle_command(
    client: Any,
    raw: str,
    session_factory: Callable[[], AbstractAsyncContextManager[Any]],
    *,
    worker_class: str,
    worker_id: str,
    secret: str,
    freshness_seconds: float,
    nonce_ttl_seconds: int,
    flag: LivePauseFlag,
    logger: logging.Logger,
    on_trigger: Callable[[str], None] | None = None,
) -> None:
    """Verify, claim, and treat a command as a nudge to re-reconcile from PG."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Discarding malformed worker command payload")
        return

    verdict = verify_command(
        secret,
        payload,
        expected_class=worker_class,
        expected_id=worker_id,
        now=time.time(),
        freshness_seconds=freshness_seconds,
    )
    if not verdict.ok:
        logger.warning("Rejected worker command: %s", verdict.reason)
        return

    if verdict.nonce is None or not await claim_nonce(client, verdict.nonce, ttl_seconds=nonce_ttl_seconds):
        logger.info("Worker command nonce already used or unclaimable; skipping")
        return

    # Trigger commands (FLUSH_NOW / POLL_NOW) carry no PG state; dispatch and return.
    if verdict.cmd in _TRIGGER_CMDS:
        if on_trigger is not None:
            on_trigger(verdict.cmd)
            logger.info("Dispatched trigger command: %s", verdict.cmd)
        else:
            logger.info("Ignoring trigger command %s (no handler configured)", verdict.cmd)
        return

    if verdict.generation is None:
        logger.warning("Rejected worker command without a parsed generation")
        return

    applied = await reconcile_worker_pause_state(
        session_factory,
        worker_class=worker_class,
        worker_id=worker_id,
        flag=flag,
        logger=logger,
        expected_generation=verdict.generation,
    )
    if applied:
        logger.info("Applied %s nudge (generation %s)", verdict.cmd, flag.applied_generation)
    else:
        logger.info(
            "Worker command was a no-op against committed state (cmd=%s generation=%s)",
            verdict.cmd,
            verdict.generation,
        )
