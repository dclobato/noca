#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the signed worker pause/resume command transport."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest

from shared.services.valkey_service.worker_commands import (
    LivePauseFlag,
    WorkerCommandType,
    _client_get_and_delete,
    build_command,
    claim_nonce,
    reconcile_worker_pause_state,
    verify_command,
    worker_command_loop,
)
from shared.services.worker_pause_state import bump_worker_pause_state

_SECRET = "super-secret-value"
_CLASS = "autojudge"
_ID = "judge-1"
_FRESHNESS = 30.0


def _command(**overrides: object) -> dict[str, object]:
    payload = build_command(
        _SECRET,
        cmd=WorkerCommandType.PAUSE,
        worker_class=_CLASS,
        worker_id=_ID,
        generation=3,
        paused_by="admin@example.com",
    )
    payload.update(overrides)
    return payload


def _verify(payload: dict[str, object], *, secret: str = _SECRET, now: float | None = None):
    return verify_command(
        secret,
        payload,
        expected_class=_CLASS,
        expected_id=_ID,
        now=now if now is not None else time.time(),
        freshness_seconds=_FRESHNESS,
    )


def test_sign_verify_round_trip() -> None:
    """A freshly built command verifies and exposes its parsed fields."""
    verdict = _verify(_command())
    assert verdict.ok
    assert verdict.reason is None
    assert verdict.cmd == "pause"
    assert verdict.generation == 3
    assert verdict.paused_by == "admin@example.com"
    assert verdict.nonce


def test_signed_resume_round_trip() -> None:
    """A resume command also verifies and reports the resume action."""
    payload = build_command(
        _SECRET,
        cmd=WorkerCommandType.RESUME,
        worker_class=_CLASS,
        worker_id=_ID,
        generation=4,
        paused_by="admin@example.com",
    )
    verdict = _verify(payload)
    assert verdict.ok
    assert verdict.cmd == "resume"


def test_build_command_rejects_empty_secret() -> None:
    """A command cannot be created when signing is disabled."""
    with pytest.raises(ValueError, match="must not be empty"):
        build_command(
            "",
            cmd=WorkerCommandType.PAUSE,
            worker_class=_CLASS,
            worker_id=_ID,
            generation=1,
            paused_by="admin@example.com",
        )


def test_wrong_target_rejected() -> None:
    """A valid signature for another worker id is rejected as wrong_target."""
    payload = build_command(
        _SECRET,
        cmd=WorkerCommandType.PAUSE,
        worker_class=_CLASS,
        worker_id="judge-2",
        generation=1,
        paused_by="a@b.c",
    )
    verdict = _verify(payload)
    assert not verdict.ok
    assert verdict.reason == "wrong_target"


def test_expired_past_rejected() -> None:
    """A stale command outside the freshness window is rejected."""
    payload = _command(issued_at=time.time() - (_FRESHNESS + 10))
    payload["sig"] = _resign(payload)
    verdict = _verify(payload)
    assert not verdict.ok
    assert verdict.reason == "expired"


def test_future_dated_rejected() -> None:
    """A future-dated command outside the freshness window is rejected."""
    payload = _command(issued_at=time.time() + (_FRESHNESS + 10))
    payload["sig"] = _resign(payload)
    verdict = _verify(payload)
    assert not verdict.ok
    assert verdict.reason == "expired"


def test_tampered_payload_rejected() -> None:
    """Mutating a signed field after signing breaks the signature."""
    payload = _command()
    payload["generation"] = 999
    verdict = _verify(payload)
    assert not verdict.ok
    assert verdict.reason == "bad_signature"


def test_wrong_secret_rejected() -> None:
    """Verifying under a different secret fails the signature check."""
    verdict = _verify(_command(), secret="different-secret")
    assert not verdict.ok
    assert verdict.reason == "bad_signature"


def test_malformed_payload_rejected() -> None:
    """A payload missing required fields is rejected as malformed."""
    verdict = _verify({"cmd": "pause", "sig": "x"})
    assert not verdict.ok
    assert verdict.reason == "malformed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cmd", "stop"),
        ("worker_class", ""),
        ("worker_id", ""),
        ("generation", -1),
        ("generation", True),
        ("paused_by", 123),
        ("issued_at", True),
        ("issued_at", float("inf")),
        ("nonce", "not-a-token-hex-nonce"),
        ("sig", 123),
    ],
)
def test_invalid_signed_field_types_are_malformed(field: str, value: object) -> None:
    """Structurally invalid signed fields are rejected before they can be used."""
    payload = _command(**{field: value})
    if field != "sig":
        payload["sig"] = _resign(payload)

    verdict = _verify(payload)

    assert not verdict.ok
    assert verdict.reason == "malformed"


def test_extra_payload_field_is_malformed() -> None:
    """Unexpected unsigned fields are rejected instead of being ignored."""
    payload = _command(extra="value")

    verdict = _verify(payload)

    assert not verdict.ok
    assert verdict.reason == "malformed"


def test_empty_secret_disabled() -> None:
    """An empty secret disables verification entirely."""
    verdict = _verify(_command(), secret="")
    assert not verdict.ok
    assert verdict.reason == "disabled"


@pytest.mark.asyncio
async def test_duplicate_nonce_rejected() -> None:
    """The second claim of the same nonce returns False."""
    client = _FakeNonceClient()
    first = await claim_nonce(client, "nonce-1", ttl_seconds=60)
    second = await claim_nonce(client, "nonce-1", ttl_seconds=60)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_nonce_store_error_fails_closed() -> None:
    """A nonce store returning None (recoverable error) fails closed."""
    client = _FakeNonceClient(error=True)
    assert await claim_nonce(client, "nonce-1", ttl_seconds=60) is False


@pytest.mark.asyncio
async def test_raw_nonce_store_requires_exact_true() -> None:
    """A raw client returning a truthy status string still fails closed."""

    class _RawClient:
        async def set(self, key: str, value: str, *, nx: bool, ex: int) -> str:
            return "OK"

    assert await claim_nonce(_RawClient(), "nonce-1", ttl_seconds=60) is False


@pytest.mark.asyncio
async def test_raw_command_client_atomically_consumes_bytes() -> None:
    """The raw Autojudge client path decodes and consumes GETDEL results."""

    class _RawClient:
        def __init__(self) -> None:
            self.value: bytes | None = b'{"cmd":"pause"}'

        async def getdel(self, key: str) -> bytes | None:
            value = self.value
            self.value = None
            return value

    client = _RawClient()

    assert await _client_get_and_delete(client, "command-key") == '{"cmd":"pause"}'
    assert await _client_get_and_delete(client, "command-key") is None


def _resign(payload: dict[str, object]) -> str:
    from shared.services.valkey_service.worker_commands import _canonical, _sign

    return _sign(_SECRET, _canonical(payload))


class _FakeNonceClient:
    """Minimal ValkeyRuntime-like client exposing set_if_absent."""

    def __init__(self, *, error: bool = False) -> None:
        self._keys: set[str] = set()
        self._error = error

    async def set_if_absent(self, key: str, value: str, *, ex: int | None = None) -> bool | None:
        if self._error:
            return None
        if key in self._keys:
            return False
        self._keys.add(key)
        return True


class _FakeLoopClient(_FakeNonceClient):
    """Nonce client that also serves a single command key value."""

    def __init__(self, command_value: str | None = None) -> None:
        super().__init__()
        self._command_value = command_value
        self.command_consumptions = 0

    async def get_and_delete(self, key: str) -> str | None:
        value = self._command_value
        self._command_value = None
        if value is not None:
            self.command_consumptions += 1
        return value


async def _run_one_iteration(
    client,
    session_factory,
    *,
    flag,
    secret,
    reconcile_seconds: float = 60.0,
) -> None:
    """Run the command loop just long enough for one reconcile/poll cycle."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        worker_command_loop(
            client,
            session_factory,
            worker_class=_CLASS,
            worker_id=_ID,
            secret=secret,
            poll_seconds=0.01,
            freshness_seconds=_FRESHNESS,
            nonce_ttl_seconds=60,
            flag=flag,
            stop_event=stop,
            logger=logging.getLogger("test"),
            reconcile_seconds=reconcile_seconds,
        )
    )
    await asyncio.sleep(0.05)
    stop.set()
    await task


@pytest.mark.asyncio
async def test_loop_reconciles_paused_state_from_pg(engine) -> None:
    """With no command present, the loop adopts committed paused state."""
    async with engine.begin() as conn:
        await bump_worker_pause_state(conn, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="a@b.c")

    flag = LivePauseFlag()
    await _run_one_iteration(
        _FakeLoopClient(),
        lambda: engine.connect(),
        flag=flag,
        secret=_SECRET,
        reconcile_seconds=0.01,
    )

    assert flag.paused is True
    assert flag.applied_generation == 1


@pytest.mark.asyncio
async def test_loop_applies_verified_command_nudge(engine, caplog: pytest.LogCaptureFixture) -> None:
    """A verified command nudges an immediate reconcile that adopts new state."""
    async with engine.begin() as conn:
        await bump_worker_pause_state(conn, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="a@b.c")

    command = build_command(
        _SECRET,
        cmd=WorkerCommandType.PAUSE,
        worker_class=_CLASS,
        worker_id=_ID,
        generation=1,
        paused_by="a@b.c",
    )
    client = _FakeLoopClient(json.dumps(command))
    flag = LivePauseFlag()
    caplog.set_level(logging.INFO, logger="test")
    await _run_one_iteration(client, lambda: engine.connect(), flag=flag, secret=_SECRET)

    assert flag.paused is True
    assert flag.applied_generation == 1
    assert client.command_consumptions == 1
    assert "Applied pause nudge (generation 1)" in caplog.messages
    assert not any("nonce already used" in message for message in caplog.messages)
    assert not any("no-op against committed state" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_loop_does_not_query_pg_on_every_command_poll() -> None:
    """Command polling does not open PostgreSQL before the fallback is due."""
    session_factory_calls = 0

    def _session_factory():
        nonlocal session_factory_calls
        session_factory_calls += 1
        raise AssertionError("PostgreSQL fallback should not run yet")

    await _run_one_iteration(
        _FakeLoopClient(),
        _session_factory,
        flag=LivePauseFlag(),
        secret=_SECRET,
        reconcile_seconds=60.0,
    )

    assert session_factory_calls == 0


@pytest.mark.asyncio
async def test_loop_stale_generation_command_is_noop(engine) -> None:
    """A command whose generation is not newer than applied changes nothing."""
    async with engine.begin() as conn:
        await bump_worker_pause_state(conn, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="a@b.c")

    command = build_command(
        _SECRET,
        cmd=WorkerCommandType.PAUSE,
        worker_class=_CLASS,
        worker_id=_ID,
        generation=1,
        paused_by="a@b.c",
    )
    client = _FakeLoopClient(json.dumps(command))
    # Already applied generation 1: the committed state is not newer, so no change.
    flag = LivePauseFlag(paused=True, paused_by="a@b.c", applied_generation=1)
    await _run_one_iteration(client, lambda: engine.connect(), flag=flag, secret=_SECRET)

    assert flag.applied_generation == 1
    assert client.command_consumptions == 1


@pytest.mark.asyncio
async def test_reconcile_rejects_nonmatching_expected_generation(engine) -> None:
    """A nudge cannot apply PostgreSQL state from a different generation."""
    async with engine.begin() as conn:
        await bump_worker_pause_state(conn, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="a@b.c")

    flag = LivePauseFlag()
    applied = await reconcile_worker_pause_state(
        lambda: engine.connect(),
        worker_class=_CLASS,
        worker_id=_ID,
        flag=flag,
        logger=logging.getLogger("test"),
        expected_generation=2,
    )

    assert applied is False
    assert flag.paused is False
    assert flag.applied_generation == 0
