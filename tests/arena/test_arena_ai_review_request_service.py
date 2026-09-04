#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The AI-review request workflow: outcomes, row locking, and the per-user cap.

The contract under test is the one issue #153 restored: a request on a pending
submission pushes **nothing** (recovery is the reconciler's alone), a first
request pushes exactly one job and charges at most one credit under a row
lock, and every authenticated request is counted per user before any lookup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services import ai_review_request_service as service
from arena.services.ai_review_request_service import AIReviewRequestOutcome, request_ai_review, submission_lookup
from shared.db_schema.arena import arena_submission_ai_reviews, arena_submissions
from shared.enumerations import Verdict
from tests.arena.test_arena_submission_routes import (
    _build_app,
    _login_user,
    _make_arena_user,
    _make_language,
    _make_problem_with_tc,
    _make_submission_with_judgment,
)

pytestmark = pytest.mark.asyncio


async def _owned_submission(session: AsyncSession, *, prefix: str, credits: int = 0, submit_to_ai: bool = False):  # type: ignore[no-untyped-def]
    author = await _make_arena_user(session, email_prefix=f"{prefix}-author")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix=f"{prefix}-owner", ai_backend_credits=credits)
    sub_id, _ = await _make_submission_with_judgment(
        session, owner, problem, lang, verdict=Verdict.WA.value, submit_to_ai=submit_to_ai
    )
    return owner, sub_id


def _set_cap(monkeypatch: pytest.MonkeyPatch, max_requests: int, *, enabled: bool = True) -> None:
    monkeypatch.setattr(service.settings, "AI_REVIEW_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(service.settings, "AI_REVIEW_RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(service.settings, "AI_REVIEW_RATE_LIMIT_WINDOW_SECONDS", 600)


# ---------------------------------------------------------------------------
# Service outcomes
# ---------------------------------------------------------------------------


async def test_the_lookup_locks_the_submission_row() -> None:
    """SQLite drops ``FOR UPDATE``; compile for PostgreSQL to prove it is there."""
    compiled = str(submission_lookup("sub-1").compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled


async def test_not_found_and_foreign_submissions(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "enqueue_arena_ai_review_job", enqueue)
    owner, sub_id = await _owned_submission(session, prefix="svc-nf")
    stranger = await _make_arena_user(session, email_prefix="svc-nf-stranger")

    assert await request_ai_review(session, object(), owner, "no-such-id") is AIReviewRequestOutcome.NOT_FOUND
    assert await request_ai_review(session, object(), stranger, sub_id) is AIReviewRequestOutcome.NOT_FOUND
    enqueue.assert_not_awaited()


async def test_completed_review_enqueues_nothing(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "enqueue_arena_ai_review_job", enqueue)
    owner, sub_id = await _owned_submission(session, prefix="svc-done", credits=3, submit_to_ai=True)
    await session.execute(
        insert(arena_submission_ai_reviews).values(
            submission_id=sub_id, ai_response="ok", ai_response_at=datetime.now(UTC), used_platform_key=True
        )
    )
    await session.commit()

    assert await request_ai_review(session, object(), owner, sub_id) is AIReviewRequestOutcome.COMPLETED
    enqueue.assert_not_awaited()
    await session.refresh(owner)
    assert owner.ai_backend_credits == 3


async def test_pending_submission_never_re_enqueues(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """The #153 bug: repeat requests used to push a duplicate job for free, every time."""
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "enqueue_arena_ai_review_job", enqueue)
    owner, sub_id = await _owned_submission(session, prefix="svc-pending", credits=2, submit_to_ai=True)

    for _ in range(5):
        assert await request_ai_review(session, object(), owner, sub_id) is AIReviewRequestOutcome.PENDING
    enqueue.assert_not_awaited()
    await session.refresh(owner)
    assert owner.ai_backend_credits == 2


async def test_insufficient_credit_writes_nothing(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "enqueue_arena_ai_review_job", enqueue)
    owner, sub_id = await _owned_submission(session, prefix="svc-broke", credits=0)

    assert await request_ai_review(session, object(), owner, sub_id) is AIReviewRequestOutcome.INSUFFICIENT_CREDIT
    enqueue.assert_not_awaited()
    flagged = await session.scalar(select(arena_submissions.c.submit_to_ai).where(arena_submissions.c.id == sub_id))
    assert flagged is False


async def test_first_request_enqueues_exactly_once_and_charges_one_credit(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "enqueue_arena_ai_review_job", enqueue)
    owner, sub_id = await _owned_submission(session, prefix="svc-first", credits=2)
    runtime = object()

    first = await request_ai_review(session, runtime, owner, sub_id)
    second = await request_ai_review(session, runtime, owner, sub_id)

    assert (first, second) == (AIReviewRequestOutcome.ENQUEUED, AIReviewRequestOutcome.PENDING)
    enqueue.assert_awaited_once()
    job = enqueue.await_args.args[1]
    assert job.submission_id == sub_id
    assert job.use_platform_key is True
    await session.refresh(owner)
    assert owner.ai_backend_credits == 1


# ---------------------------------------------------------------------------
# Per-user cap on the route
# ---------------------------------------------------------------------------


async def test_cap_counts_every_request_per_user_before_any_lookup(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "enqueue_arena_ai_review_job", enqueue)
    _set_cap(monkeypatch, 2)
    owner, sub_id = await _owned_submission(session, prefix="cap", credits=5)
    other, other_sub = await _owned_submission(session, prefix="cap-other", credits=5)
    app = _build_app(session, valkey_runtime=MagicMock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, owner)
        # A not-found request spends budget too: the count happens before the lookup.
        assert (await client.post("/submissions/nope/request-ai-review", follow_redirects=False)).status_code == 404
        assert (
            await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)
        ).status_code == 303
        over = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)
        # Over budget, even an unknown id is not looked up: same 303, no 404.
        over_unknown = await client.post("/submissions/nope/request-ai-review", follow_redirects=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, other)
        unaffected = await client.post(f"/submissions/{other_sub}/request-ai-review", follow_redirects=False)

    assert over.status_code == 303 and f"/submissions/{sub_id}" in over.headers["location"]
    assert over_unknown.status_code == 303
    assert unaffected.status_code == 303
    # Exactly two jobs ever: the owner's first real request and the other user's.
    assert enqueue.await_count == 2
    await session.refresh(owner)
    assert owner.ai_backend_credits == 4


async def test_cap_can_be_disabled(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "enqueue_arena_ai_review_job", enqueue)
    _set_cap(monkeypatch, 1, enabled=False)
    owner, sub_id = await _owned_submission(session, prefix="cap-off", credits=1)
    app = _build_app(session, valkey_runtime=MagicMock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, owner)
        for _ in range(4):
            resp = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)
            assert resp.status_code == 303
    enqueue.assert_awaited_once()
