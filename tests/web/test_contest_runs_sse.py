import asyncio
import json
from types import SimpleNamespace

import pytest

from shared.enumerations import RoleEnum
from shared.queue_schema import VerdictEvent
from web.routes.contest_runs import _iter_verdict_sse_events


class _FakeRuntime:
    def __init__(self, events: list[VerdictEvent]) -> None:
        self.events = events
        self.ready = asyncio.Event()
        self.cancelled = False

    async def iter_verdict_events(self):  # type: ignore[override]
        try:
            await self.ready.wait()
            for event in self.events:
                yield event
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _make_event(*, contest_id: str = "contest-1") -> VerdictEvent:
    return VerdictEvent(
        submission_id="sub-1",
        contest_id=contest_id,
        judgment_id="judgment-1",
        verdict="AC",
        team_id="team-1",
        problem_id="problem-a",
        update_kind="autojudge",
    )


def _make_actor(role: RoleEnum) -> SimpleNamespace:
    return SimpleNamespace(role=role)


def _make_contest(*, frozen: bool) -> SimpleNamespace:
    return SimpleNamespace(is_scoreboard_frozen=frozen)


@pytest.mark.asyncio
async def test_heartbeat_does_not_cancel_pending_pubsub_read_and_live_payload_is_json() -> None:
    runtime = _FakeRuntime([_make_event()])
    disconnected = False

    async def is_disconnected() -> bool:
        return disconnected

    stream = _iter_verdict_sse_events(
        runtime=runtime,
        contest_id="contest-1",
        actor=_make_actor(RoleEnum.TEAM),
        contest=_make_contest(frozen=False),
        is_disconnected=is_disconnected,
        heartbeat_interval_seconds=0.01,
        reconnect_delay_seconds=0,
    )

    first_chunk = await anext(stream)
    assert first_chunk == "data: ping\n\n"
    assert runtime.cancelled is False

    runtime.ready.set()
    second_chunk = await anext(stream)
    assert second_chunk.startswith("data: ")
    assert json.loads(second_chunk.removeprefix("data: ").strip()) == {
        "kind": "verdict-update",
        "update_kind": "autojudge",
        "team_id": "team-1",
        "problem_id": "problem-a",
        "verdict": "AC",
        "contest_id": "contest-1",
    }
    assert runtime.cancelled is False

    disconnected = True
    await stream.aclose()
    assert runtime.cancelled is False


@pytest.mark.asyncio
async def test_contest_filtering_skips_other_contests() -> None:
    runtime = _FakeRuntime([_make_event(contest_id="contest-2"), _make_event(contest_id="contest-1")])
    runtime.ready.set()

    async def is_disconnected() -> bool:
        return False

    stream = _iter_verdict_sse_events(
        runtime=runtime,
        contest_id="contest-1",
        actor=_make_actor(RoleEnum.TEAM),
        contest=_make_contest(frozen=False),
        is_disconnected=is_disconnected,
        heartbeat_interval_seconds=1,
        reconnect_delay_seconds=0,
    )

    chunk = await anext(stream)
    assert json.loads(chunk.removeprefix("data: ").strip())["contest_id"] == "contest-1"
    await stream.aclose()


@pytest.mark.asyncio
async def test_team_receives_redacted_payload_when_scoreboard_is_frozen() -> None:
    runtime = _FakeRuntime([_make_event()])
    runtime.ready.set()

    async def is_disconnected() -> bool:
        return False

    stream = _iter_verdict_sse_events(
        runtime=runtime,
        contest_id="contest-1",
        actor=_make_actor(RoleEnum.TEAM),
        contest=_make_contest(frozen=True),
        is_disconnected=is_disconnected,
        reconnect_delay_seconds=0,
    )

    chunk = await anext(stream)
    assert json.loads(chunk.removeprefix("data: ").strip()) == {"kind": "verdict-update"}
    await stream.aclose()


@pytest.mark.asyncio
async def test_privileged_role_receives_live_payload_even_when_scoreboard_is_frozen() -> None:
    runtime = _FakeRuntime([_make_event()])
    runtime.ready.set()

    async def is_disconnected() -> bool:
        return False

    stream = _iter_verdict_sse_events(
        runtime=runtime,
        contest_id="contest-1",
        actor=_make_actor(RoleEnum.ADMIN),
        contest=_make_contest(frozen=True),
        is_disconnected=is_disconnected,
        reconnect_delay_seconds=0,
    )

    chunk = await anext(stream)
    assert json.loads(chunk.removeprefix("data: ").strip()) == {
        "kind": "verdict-update",
        "update_kind": "autojudge",
        "team_id": "team-1",
        "problem_id": "problem-a",
        "verdict": "AC",
        "contest_id": "contest-1",
    }
    await stream.aclose()


@pytest.mark.asyncio
async def test_judge_receives_live_payload_even_when_scoreboard_is_frozen() -> None:
    runtime = _FakeRuntime([_make_event()])
    runtime.ready.set()

    async def is_disconnected() -> bool:
        return False

    stream = _iter_verdict_sse_events(
        runtime=runtime,
        contest_id="contest-1",
        actor=_make_actor(RoleEnum.JUDGE),
        contest=_make_contest(frozen=True),
        is_disconnected=is_disconnected,
        reconnect_delay_seconds=0,
    )

    chunk = await anext(stream)
    assert json.loads(chunk.removeprefix("data: ").strip()) == {
        "kind": "verdict-update",
        "update_kind": "autojudge",
        "team_id": "team-1",
        "problem_id": "problem-a",
        "verdict": "AC",
        "contest_id": "contest-1",
    }
    await stream.aclose()
