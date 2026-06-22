#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena per-user submission status snapshot and SSE endpoints."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.user_submission_status import (
    arena_user_submissions_events,
    get_streaming_arena_user,
    parse_submission_ids,
    resolve_owned_submission_ids,
)
from arena.routes.user_submission_status import (
    router as user_submission_status_router,
)
from arena.services.pagination_service import build_pagination_params
from arena.services.submission_list_service import get_user_submissions
from shared.db_schema.arena.arena_submissions import (
    arena_submission_judgments,
    arena_submissions,
)
from shared.enumerations import ArenaRole, JudgmentStatus, Verdict
from shared.queue_schema import ArenaVerdictEvent
from web.models.language import Language


async def _make_language(session: AsyncSession) -> Language:
    """Create and flush an active language for submission rows."""
    language = Language(
        id=f"arena-test-{uuid.uuid4().hex[:8]}",
        name="Arena Test Language",
        icon="devicon-python-plain",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_user(session: AsyncSession) -> ArenaUser:
    """Create and flush an active Arena user."""
    user = ArenaUser(
        nome="Status Watcher",
        email_normalizado=f"watch-{uuid.uuid4().hex[:8]}@test.example",
        dta_nascimento=date(1998, 1, 1),
        role=ArenaRole.ARENA_USER,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_problem(session: AsyncSession, owner: ArenaUser) -> ArenaProblem:
    """Create and flush an Arena problem."""
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
        title=f"Status Problem {uuid.uuid4().hex[:6]}",
        owner_id=owner.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    return problem


async def _make_submission(
    session: AsyncSession,
    *,
    user: ArenaUser,
    problem: ArenaProblem,
    language: Language,
    status: JudgmentStatus,
    verdict: Verdict | None,
    max_wall_time_ms: int | None = None,
) -> str:
    """Insert a submission plus a single judgment and return the submission id."""
    submission_id = str(uuid.uuid4())
    await session.execute(
        arena_submissions.insert().values(
            id=submission_id,
            user_id=user.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code="print(1)\n",
            source_hash=uuid.uuid4().hex,
            source_size_bytes=8,
        )
    )
    await session.execute(
        arena_submission_judgments.insert().values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=status.value,
            autojudge_verdict=verdict.value if verdict else None,
            final_verdict=verdict.value if verdict else None,
            max_wall_time_ms=max_wall_time_ms,
        )
    )
    await session.flush()
    return submission_id


def _build_app(
    session: AsyncSession,
    user: ArenaUser | None,
    *,
    valkey_runtime: object | None = None,
) -> FastAPI:
    """Build a minimal app exposing only the status router, wired to ``session``.

    The snapshot route uses the request-scoped ``get_db`` session (overridden to
    the test session). The SSE route resolves auth/ownership in its own short-lived
    session opened from ``app.state.arena_db_session``; that factory and an optional
    fake ``valkey_runtime`` are provided here.
    """
    app = FastAPI()
    app.include_router(user_submission_status_router)
    app.dependency_overrides[get_db] = lambda: session
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    if valkey_runtime is not None:
        app.state.valkey_runtime = valkey_runtime
    if user is not None:
        app.dependency_overrides[get_current_arena_user] = lambda: user
        app.dependency_overrides[get_streaming_arena_user] = lambda: user
    return app


# ── parse_submission_ids (pure) ────────────────────────────────────────────


def test_parse_submission_ids_empty_returns_empty() -> None:
    """Absent or blank input yields an empty list."""
    assert parse_submission_ids(None) == []
    assert parse_submission_ids("") == []
    assert parse_submission_ids("  ,  ,") == []


def test_parse_submission_ids_dedupes_preserving_order() -> None:
    """Valid UUIDs are returned once, in first-seen order."""
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    assert parse_submission_ids(f"{a},{b},{a}") == [a, b]


def test_parse_submission_ids_rejects_malformed() -> None:
    """Any non-UUID token raises 400."""
    with pytest.raises(HTTPException) as exc:
        parse_submission_ids(f"{uuid.uuid4()},not-a-uuid")
    assert exc.value.status_code == 400


def test_parse_submission_ids_rejects_over_limit() -> None:
    """Exceeding the cap raises 400."""
    ids = ",".join(str(uuid.uuid4()) for _ in range(4))
    with pytest.raises(HTTPException) as exc:
        parse_submission_ids(ids, limit=3)
    assert exc.value.status_code == 400


# ── resolve_owned_submission_ids (ownership gate) ───────────────────────────


@pytest.mark.asyncio
async def test_resolve_owned_submission_ids_excludes_foreign(session: AsyncSession) -> None:
    """Only the requesting user's own submission IDs are returned (the SSE gate)."""
    language = await _make_language(session)
    owner = await _make_user(session)
    other = await _make_user(session)
    problem = await _make_problem(session, owner)

    mine = await _make_submission(
        session,
        user=owner,
        problem=problem,
        language=language,
        status=JudgmentStatus.QUEUED,
        verdict=None,
    )
    theirs = await _make_submission(
        session,
        user=other,
        problem=problem,
        language=language,
        status=JudgmentStatus.QUEUED,
        verdict=None,
    )

    owned = await resolve_owned_submission_ids(session, user_id=owner.id, candidate_ids=[mine, theirs])

    assert owned == frozenset({mine})
    # The verdict-event predicate is pure membership over this set: an event for a
    # foreign (or unknown) submission can never match.
    assert mine in owned
    assert theirs not in owned
    assert str(uuid.uuid4()) not in owned


@pytest.mark.asyncio
async def test_resolve_owned_submission_ids_empty_candidates(session: AsyncSession) -> None:
    """No candidates resolves to an empty owned set without touching the database."""
    user = await _make_user(session)
    assert await resolve_owned_submission_ids(session, user_id=user.id, candidate_ids=[]) == frozenset()


# ── is_final via get_user_submissions (terminal verdictless statuses) ───────


@pytest.mark.asyncio
async def test_is_final_true_for_verdictless_failed(session: AsyncSession) -> None:
    """A FAILED judgment with no verdict is final (regression: not keyed off verdict)."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)
    await _make_submission(
        session,
        user=user,
        problem=problem,
        language=language,
        status=JudgmentStatus.FAILED,
        verdict=None,
    )

    page = await get_user_submissions(
        session=session, user_id=user.id, params=build_pagination_params(None, per_page=25)
    )

    assert len(page.items) == 1
    row = page.items[0]
    assert row.verdict is None
    assert row.status == JudgmentStatus.FAILED.value
    assert row.is_final is True


@pytest.mark.asyncio
async def test_is_final_false_for_pending(session: AsyncSession) -> None:
    """A JUDGING judgment is non-final."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)
    await _make_submission(
        session,
        user=user,
        problem=problem,
        language=language,
        status=JudgmentStatus.JUDGING,
        verdict=None,
    )

    page = await get_user_submissions(
        session=session, user_id=user.id, params=build_pagination_params(None, per_page=25)
    )

    assert page.items[0].is_final is False


# ── status.json route ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_json_requires_authentication(session: AsyncSession) -> None:
    """Guests get a clean 401, not a redirect."""
    app = _build_app(session, user=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/user/submissions/status.json?ids={uuid.uuid4()}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_json_rejects_malformed_ids(session: AsyncSession) -> None:
    """Malformed IDs are rejected with 400."""
    user = await _make_user(session)
    app = _build_app(session, user=user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/submissions/status.json?ids=not-a-uuid")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_status_json_scopes_to_owner_and_reports_finality(session: AsyncSession) -> None:
    """Snapshot returns only the owner's rows with correct verdict/badge/finality."""
    language = await _make_language(session)
    owner = await _make_user(session)
    other = await _make_user(session)
    problem = await _make_problem(session, owner)

    accepted = await _make_submission(
        session,
        user=owner,
        problem=problem,
        language=language,
        status=JudgmentStatus.DONE,
        verdict=Verdict.AC,
        max_wall_time_ms=42,
    )
    foreign = await _make_submission(
        session,
        user=other,
        problem=problem,
        language=language,
        status=JudgmentStatus.DONE,
        verdict=Verdict.AC,
    )

    app = _build_app(session, user=owner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/user/submissions/status.json?ids={accepted},{foreign}")

    assert response.status_code == 200
    submissions = response.json()["submissions"]
    assert [s["submission_id"] for s in submissions] == [accepted]
    row = submissions[0]
    assert row["verdict"] == "AC"
    assert row["verdict_label"] == "Accepted"
    assert row["verdict_badge_class"] == "bg-success"
    assert row["is_final"] is True
    assert row["max_wall_time_ms"] == 42


@pytest.mark.asyncio
async def test_status_json_empty_ids_returns_empty(session: AsyncSession) -> None:
    """No requested IDs returns an empty list without a query."""
    user = await _make_user(session)
    app = _build_app(session, user=user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/submissions/status.json")
    assert response.status_code == 200
    assert response.json() == {"submissions": []}


# ── status/events route ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_events_requires_authentication(session: AsyncSession) -> None:
    """Guests get a clean 401 on the SSE endpoint before any stream opens."""
    app = _build_app(session, user=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/user/submissions/status/events?ids={uuid.uuid4()}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_events_rejects_malformed_ids(session: AsyncSession) -> None:
    """Malformed IDs are rejected with 400 before any stream opens."""
    user = await _make_user(session)
    app = _build_app(session, user=user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/submissions/status/events?ids=not-a-uuid")
    assert response.status_code == 400


class _FakeRuntime:
    """Minimal valkey_runtime stub yielding a fixed sequence of verdict events."""

    def __init__(self, events: list[ArenaVerdictEvent]) -> None:
        self._events = events

    async def iter_arena_verdict_events(self) -> AsyncIterator[ArenaVerdictEvent]:
        for event in self._events:
            yield event
        # Block after the seeded events so the test, not StopAsyncIteration, drives
        # termination via is_disconnected. Cancelled when the body iterator closes.
        await asyncio.Event().wait()


class _FakeRequest:
    """Drives the SSE generator deterministically by scripting is_disconnected."""

    def __init__(self, app_state: object, *, connected_calls: int) -> None:
        self.app = type("_App", (), {"state": app_state})()
        self._calls = 0
        self._connected_calls = connected_calls

    async def is_disconnected(self) -> bool:
        """Report connected for the first ``connected_calls`` checks, then dropped."""
        self._calls += 1
        return self._calls > self._connected_calls


async def _drain_body(response: object) -> list[str]:
    """Collect ``data:`` payloads emitted by a StreamingResponse body iterator."""
    frames: list[str] = []
    async for chunk in response.body_iterator:  # type: ignore[attr-defined]
        for line in chunk.splitlines():
            if line.startswith("data:"):
                frames.append(line[len("data:") :].strip())
    return frames


async def _run_events_stream(
    session: AsyncSession,
    *,
    owner: ArenaUser,
    watched_id: str,
    event_submission_id: str,
) -> list[str]:
    """Invoke the SSE endpoint with a fake request and return its emitted frames.

    ``connected_calls=3`` lets the generator emit its initial ping and process
    exactly one event (consuming it whether or not it matches) before the next
    is_disconnected check ends the stream — so an ignored event is proven consumed,
    not merely raced past.
    """
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    app_state = type("_State", (), {})()
    app_state.arena_db_session = factory  # type: ignore[attr-defined]
    app_state.valkey_runtime = _FakeRuntime(  # type: ignore[attr-defined]
        [ArenaVerdictEvent(submission_id=event_submission_id, judgment_id=str(uuid.uuid4()), verdict="AC")]
    )
    request = _FakeRequest(app_state, connected_calls=3)

    response = await arena_user_submissions_events(request=request, ids=watched_id, current_user=owner)  # type: ignore[arg-type]
    return await _drain_body(response)


@pytest.mark.asyncio
async def test_status_events_emits_refresh_for_owned_event(session: AsyncSession) -> None:
    """A verdict event for an owned, watched submission produces a refresh frame."""
    language = await _make_language(session)
    owner = await _make_user(session)
    problem = await _make_problem(session, owner)
    owned = await _make_submission(
        session,
        user=owner,
        problem=problem,
        language=language,
        status=JudgmentStatus.JUDGING,
        verdict=None,
    )
    await session.commit()  # the SSE route resolves ownership in its own session

    frames = await _run_events_stream(session, owner=owner, watched_id=owned, event_submission_id=owned)

    assert frames == ["ping", "refresh"]


@pytest.mark.asyncio
async def test_status_events_ignores_unowned_event(session: AsyncSession) -> None:
    """A verdict event for a submission the watcher does not own yields no refresh."""
    language = await _make_language(session)
    owner = await _make_user(session)
    problem = await _make_problem(session, owner)
    owned = await _make_submission(
        session,
        user=owner,
        problem=problem,
        language=language,
        status=JudgmentStatus.JUDGING,
        verdict=None,
    )
    await session.commit()

    # The event references a different submission id than the one being watched.
    frames = await _run_events_stream(session, owner=owner, watched_id=owned, event_submission_id=str(uuid.uuid4()))

    assert "refresh" not in frames
    assert frames == ["ping"]  # only the initial connect ping; the event was ignored
