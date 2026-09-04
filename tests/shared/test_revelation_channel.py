#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reveal channel/key builders, the changed-event contract, and pub/sub round-trip."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from shared.reveal_schema import (
    GLOBAL_SCOPE,
    REVEAL_EVENT_VERSION,
    RevealMediaAction,
    RevealMediaCueEvent,
    RevealStateChangedEvent,
    parse_revelation_event,
)
from shared.services.valkey_service import (
    REVELATION_CHANNEL_PREFIX,
    InvalidRevelationScopeError,
    reveal_controller_key,
    reveal_lock_key,
    reveal_state_key,
    revelation_channel,
    validate_component,
)
from shared.services.valkey_service.runtime import ValkeyRuntime
from web.config import settings


def _make_cue(
    contest_id: str,
    scope: str,
    action: RevealMediaAction,
    team_id: str | None,
) -> RevealMediaCueEvent:
    return RevealMediaCueEvent(
        contest_id=contest_id,
        scope=scope,
        action=action,
        team_id=team_id,
        published_at=datetime.now(UTC),
    )


def _make_event(contest_id: str, scope: str) -> RevealStateChangedEvent:
    return RevealStateChangedEvent(
        contest_id=contest_id,
        scope=scope,
        command="step",
        phase="revealing",
        focused_team_id=str(uuid4()),
        revealed_count=3,
        frozen_count=7,
        published_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Key / channel builders
# ---------------------------------------------------------------------------


def test_state_key_format() -> None:
    assert reveal_state_key("abc", "global") == "animator:reveal:abc:global"


def test_lock_key_format() -> None:
    assert reveal_lock_key("abc", "s1") == "animator:reveal:lock:abc:s1"


def test_controller_key_format() -> None:
    assert reveal_controller_key("abc", "global") == "animator:reveal:controller:abc:global"


def test_channel_format() -> None:
    assert revelation_channel("abc", "global") == f"{REVELATION_CHANNEL_PREFIX}:abc:global"


def test_keys_and_channel_embed_both_components() -> None:
    cid, scope = str(uuid4()), str(uuid4())
    for built in (
        reveal_state_key(cid, scope),
        reveal_lock_key(cid, scope),
        reveal_controller_key(cid, scope),
        revelation_channel(cid, scope),
    ):
        assert cid in built
        assert scope in built


def test_scopes_isolate_channels_for_same_contest() -> None:
    cid = "c1"
    assert revelation_channel(cid, GLOBAL_SCOPE) != revelation_channel(cid, "site-x")


def test_global_scope_is_a_valid_component() -> None:
    assert validate_component("scope", GLOBAL_SCOPE) == GLOBAL_SCOPE


# ---------------------------------------------------------------------------
# Component validation / injection rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "has:colon",
        "with space",
        "star*",
        "slash/here",
        "",
        "x" * 65,
        "brace{}",
    ],
)
def test_injection_and_malformed_components_are_rejected(bad: str) -> None:
    with pytest.raises(InvalidRevelationScopeError):
        validate_component("scope", bad)


def test_builders_reject_injection_before_building() -> None:
    with pytest.raises(InvalidRevelationScopeError):
        reveal_state_key("ok", "evil:extra:segment")
    with pytest.raises(InvalidRevelationScopeError):
        revelation_channel("bad id", "global")


# ---------------------------------------------------------------------------
# Event version round-trip
# ---------------------------------------------------------------------------


def test_event_round_trips_through_json() -> None:
    event = _make_event(str(uuid4()), GLOBAL_SCOPE)
    restored = RevealStateChangedEvent.model_validate_json(event.model_dump_json())
    assert restored == event
    assert restored.event_version == REVEAL_EVENT_VERSION


def test_foreign_event_version_is_rejected() -> None:
    event = _make_event("c", "global")
    payload = event.model_dump()
    payload["event_version"] = 2
    with pytest.raises(ValueError):
        RevealStateChangedEvent.model_validate(payload)


def test_event_forbids_extra_fields() -> None:
    event = _make_event("c", "global")
    payload = event.model_dump()
    payload["surprise"] = "x"
    with pytest.raises(ValueError):
        RevealStateChangedEvent.model_validate(payload)


# ---------------------------------------------------------------------------
# The channel's second payload: transient media cues
# ---------------------------------------------------------------------------


def test_media_cue_round_trips_through_json() -> None:
    cue = _make_cue("c", GLOBAL_SCOPE, "show", "team-1")
    restored = RevealMediaCueEvent.model_validate_json(cue.model_dump_json())
    assert restored == cue
    assert restored.event_version == REVEAL_EVENT_VERSION


def test_the_two_payloads_cannot_be_mistaken_for_each_other() -> None:
    """The whole compatibility argument for discriminating by shape, pinned.

    Both models forbid extra fields and their required fields are disjoint, so
    trying them in turn cannot mis-assign a frame. This is what lets the channel
    carry a second payload **without** adding a discriminator field to
    :class:`RevealStateChangedEvent` — which would be a breaking change, since an
    already-deployed replica's ``extra="forbid"`` would reject every new nudge
    and silently freeze its projectors mid-ceremony.
    """
    nudge = _make_event("c", GLOBAL_SCOPE)
    cue = _make_cue("c", GLOBAL_SCOPE, "show", "team-1")

    with pytest.raises(ValueError):
        RevealStateChangedEvent.model_validate_json(cue.model_dump_json())
    with pytest.raises(ValueError):
        RevealMediaCueEvent.model_validate_json(nudge.model_dump_json())


def test_parse_revelation_event_returns_the_right_model() -> None:
    nudge = _make_event("c", GLOBAL_SCOPE)
    cue = _make_cue("c", GLOBAL_SCOPE, "hide", None)

    assert parse_revelation_event(nudge.model_dump_json()) == nudge
    assert parse_revelation_event(cue.model_dump_json()) == cue


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "{}",
        '{"event_version": 2, "contest_id": "c", "scope": "global", "action": "show", '
        '"team_id": null, "published_at": "2026-01-01T00:00:00Z"}',
        '{"contest_id": "c", "scope": "global", "action": "sideways", '
        '"team_id": null, "published_at": "2026-01-01T00:00:00Z"}',
    ],
)
def test_parse_revelation_event_returns_none_for_anything_else(payload: str) -> None:
    """A frame this build cannot read is dropped, never raised.

    That is what lets a newer producer add a payload shape without a lockstep
    deploy: an older replica simply ignores the frame it does not understand.
    """
    assert parse_revelation_event(payload) is None


@pytest.mark.asyncio
async def test_unstarted_runtime_does_not_signal_a_subscription() -> None:
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    subscribed = False

    def note_subscribed() -> None:
        nonlocal subscribed
        subscribed = True

    events = runtime.iter_revelation_events(str(uuid4()), GLOBAL_SCOPE, on_subscribed=note_subscribed)
    assert [event async for event in events] == []
    assert subscribed is False


# ---------------------------------------------------------------------------
# Real-Valkey pub/sub round-trip, scope isolation, malformed skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revelation_round_trips_through_channel(valkey_client: aivalkey.Valkey) -> None:
    """An event published via publish_revelation is received by iter_revelation_events."""
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()

    contest_id, scope = str(uuid4()), GLOBAL_SCOPE
    event = _make_event(contest_id, scope)
    agen = runtime.iter_revelation_events(contest_id, scope)
    next_task = asyncio.create_task(agen.__anext__())
    try:
        received: RevealStateChangedEvent | None = None
        for _ in range(20):
            await runtime.publish_revelation(event)
            done, _pending = await asyncio.wait({next_task}, timeout=0.1)
            if done:
                received = next_task.result()
                break
        assert received is not None, "Revelation event was not received on the channel"
        assert received == event
    finally:
        if not next_task.done():
            next_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await next_task
        with contextlib.suppress(Exception):
            await agen.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_scopes_are_isolated_on_the_wire(valkey_client: aivalkey.Valkey) -> None:
    """A subscriber on one scope never receives another scope's events."""
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()

    contest_id = str(uuid4())
    mine = _make_event(contest_id, "site-a")
    other = _make_event(contest_id, "site-b")
    agen = runtime.iter_revelation_events(contest_id, "site-a")
    next_task = asyncio.create_task(agen.__anext__())
    try:
        received: RevealStateChangedEvent | None = None
        for _ in range(20):
            await runtime.publish_revelation(other)  # wrong scope, must be ignored
            await runtime.publish_revelation(mine)
            done, _pending = await asyncio.wait({next_task}, timeout=0.1)
            if done:
                received = next_task.result()
                break
        assert received is not None, "Own-scope event was not received"
        assert received.scope == "site-a"
    finally:
        if not next_task.done():
            next_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await next_task
        with contextlib.suppress(Exception):
            await agen.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_publish_reports_success_with_zero_subscribers(valkey_client: aivalkey.Valkey) -> None:
    """publish_revelation returns True even when no subscriber is listening."""
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()
    try:
        delivered = await runtime.publish_revelation(_make_event(str(uuid4()), GLOBAL_SCOPE))
        assert delivered is True
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_malformed_and_foreign_frames_are_skipped_not_fatal(valkey_client: aivalkey.Valkey) -> None:
    """A non-JSON frame and a foreign-version frame are dropped; a valid one still arrives."""
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()

    contest_id, scope = str(uuid4()), GLOBAL_SCOPE
    channel = revelation_channel(contest_id, scope)
    good = _make_event(contest_id, scope)
    foreign = good.model_dump(mode="json")
    foreign["event_version"] = 2

    agen = runtime.iter_revelation_events(contest_id, scope)
    next_task = asyncio.create_task(agen.__anext__())
    try:
        received: RevealStateChangedEvent | None = None
        for _ in range(20):
            await valkey_client.publish(channel, "not-json")
            await valkey_client.publish(channel, json.dumps(foreign))  # valid JSON, wrong version
            await runtime.publish_revelation(good)
            done, _pending = await asyncio.wait({next_task}, timeout=0.1)
            if done:
                received = next_task.result()
                break
        assert received is not None, "Valid event was not received after malformed frames"
        assert received == good
    finally:
        if not next_task.done():
            next_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await next_task
        with contextlib.suppress(Exception):
            await agen.aclose()
        await runtime.stop()
