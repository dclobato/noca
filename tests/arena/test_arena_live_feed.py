#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the public Arena live submission feed snapshot."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
import arena.services.live_feed_service as live_feed_module
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionJudgment
from arena.models.arena_users import ArenaUser
from arena.services.live_feed_service import build_arena_live_feed_snapshot
from shared.enumerations import ArenaRole, JudgmentStatus
from shared.services.sse_refresh import iter_refresh_events
from web.models.language import Language

_BASE_TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _NeverYieldRuntime:
    """Fake Valkey runtime that never emits verdict events."""

    async def iter_arena_verdict_events(self):  # type: ignore[no-untyped-def]
        """Yield no events while keeping the async generator shape."""
        if False:
            yield None


async def _make_language(session: AsyncSession) -> Language:
    language = Language(
        id=f"lf-{uuid.uuid4().hex[:8]}",
        name="Live Feed Language",
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
    user = ArenaUser(
        nome="Live Feed User",
        email_normalizado=f"lf-{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1998, 1, 1),
        role=ArenaRole.ARENA_USER,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_affiliation(session: AsyncSession) -> ArenaAffiliation:
    affiliation = ArenaAffiliation(
        name=f"Live Feed Affiliation {uuid.uuid4().hex[:8]}",
        country_code="BR",
        logo_base64="logo",
        logo_mime="image/png",
    )
    session.add(affiliation)
    await session.flush()
    return affiliation


async def _make_problem(session: AsyncSession, author: ArenaUser) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
        title=f"Problem {uuid.uuid4().hex[:6]}",
        owner_id=author.id,
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
    created_at: datetime,
    status: JudgmentStatus,
    final_verdict: str | None,
) -> ArenaSubmission:
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="x",
        source_hash=uuid.uuid4().hex,
        source_size_bytes=1,
        submit_to_ai=False,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=status.value,
        autojudge_verdict=final_verdict,
        final_verdict=final_verdict,
        created_at=created_at,
    )
    session.add(judgment)
    await session.flush()
    return submission


@pytest.mark.asyncio
async def test_live_events_yields_initial_ping_without_waiting() -> None:
    """The SSE stream starts with a ping so EventSource opens immediately."""

    async def is_disconnected() -> bool:
        return False

    runtime = _NeverYieldRuntime()
    chunks = iter_refresh_events(
        open_event_stream=runtime.iter_arena_verdict_events,
        is_disconnected=is_disconnected,
        emit_initial_ping=True,
    )

    try:
        assert await anext(chunks) == "data: ping\n\n"
    finally:
        await chunks.aclose()


@pytest.mark.asyncio
async def test_live_feed_returns_only_finalized_newest_first(session: AsyncSession) -> None:
    """Only finalized (final_verdict set) submissions appear, newest first."""
    language = await _make_language(session)
    user = await _make_user(session)
    affiliation = await _make_affiliation(session)
    user.affiliation_id = affiliation.id
    user.country_code = "BR"
    problem = await _make_problem(session, user)

    finalized_ac = await _make_submission(
        session,
        user=user,
        problem=problem,
        language=language,
        created_at=_BASE_TS,
        status=JudgmentStatus.DONE,
        final_verdict="AC",
    )
    finalized_wa = await _make_submission(
        session,
        user=user,
        problem=problem,
        language=language,
        created_at=_BASE_TS + timedelta(minutes=5),
        status=JudgmentStatus.DONE,
        final_verdict="WA",
    )
    # Not finalized yet (queued, no verdict) — must be excluded.
    await _make_submission(
        session,
        user=user,
        problem=problem,
        language=language,
        created_at=_BASE_TS + timedelta(minutes=10),
        status=JudgmentStatus.QUEUED,
        final_verdict=None,
    )

    rows = (await build_arena_live_feed_snapshot(session)).rows

    assert [row.submission_id for row in rows] == [finalized_wa.id, finalized_ac.id]
    assert rows[0].verdict == "WA"
    assert rows[0].affiliation_id == affiliation.id
    assert rows[0].affiliation_name == affiliation.name
    assert rows[0].affiliation_has_logo is True
    assert rows[0].country_code == "BR"
    assert rows[0].country_name == "Brazil"
    assert rows[0].language_icon == "devicon-python-plain"
    assert rows[0].problem_number == problem.arena_number


@pytest.mark.asyncio
async def test_live_feed_preserves_missing_user_origin_data(session: AsyncSession) -> None:
    """Missing affiliation and country remain nullable for client placeholders."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)
    await _make_submission(
        session,
        user=user,
        problem=problem,
        language=language,
        created_at=_BASE_TS,
        status=JudgmentStatus.DONE,
        final_verdict="AC",
    )

    row = (await build_arena_live_feed_snapshot(session)).rows[0]

    assert row.affiliation_id is None
    assert row.affiliation_name is None
    assert row.affiliation_has_logo is False
    assert row.country_code is None
    assert row.country_name is None


@pytest.mark.asyncio
async def test_live_feed_caps_at_twenty_finalized_rows(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The newest 20 finalized rows are returned even when more exist, never fewer."""
    monkeypatch.setattr(live_feed_module.settings, "ARENA_LIVE_FEED_LIMIT", 20)
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    created_ids: list[str] = []
    for i in range(25):
        submission = await _make_submission(
            session,
            user=user,
            problem=problem,
            language=language,
            created_at=_BASE_TS + timedelta(minutes=i),
            status=JudgmentStatus.DONE,
            final_verdict="AC",
        )
        created_ids.append(submission.id)

    rows = (await build_arena_live_feed_snapshot(session)).rows

    assert len(rows) == 20
    # Newest 20 by created_at, descending.
    expected = list(reversed(created_ids))[:20]
    assert [row.submission_id for row in rows] == expected


@pytest.mark.asyncio
async def test_live_feed_snapshot_reports_hidden_older_rows(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot metadata reports when older rows exist beyond the configured limit."""
    monkeypatch.setattr(live_feed_module.settings, "ARENA_LIVE_FEED_LIMIT", 3)
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    created_ids: list[str] = []
    for i in range(4):
        submission = await _make_submission(
            session,
            user=user,
            problem=problem,
            language=language,
            created_at=_BASE_TS + timedelta(minutes=i),
            status=JudgmentStatus.DONE,
            final_verdict="AC",
        )
        created_ids.append(submission.id)

    snapshot = await build_arena_live_feed_snapshot(session)

    assert snapshot.limit == 3
    assert snapshot.has_more is True
    assert [row.submission_id for row in snapshot.rows] == list(reversed(created_ids))[:3]


@pytest.mark.asyncio
async def test_live_feed_ignores_superseded_judgment(session: AsyncSession) -> None:
    """A superseded judgment must not surface its verdict."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    submission = await _make_submission(
        session,
        user=user,
        problem=problem,
        language=language,
        created_at=_BASE_TS,
        status=JudgmentStatus.SUPERSEDED,
        final_verdict="AC",
    )

    rows = (await build_arena_live_feed_snapshot(session)).rows

    assert all(row.submission_id != submission.id for row in rows)
    assert rows == []
