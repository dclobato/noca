#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``POST /admin/problems/{id}/rejudge-all`` after issue #156.

Contract: the password check runs on the shared verification throttle (a
wrong password is counted, and past the cap the ``429`` page answers before the
password is read); submissions with an in-flight judgment are skipped rather
than superseded, so a repeat click queues nothing new; the per-problem cooldown
refuses a repeat; and an accepted action writes one warning ``admin_action``
row. Arena route tests carry no Valkey, so the cooldown runs on its
process-local window, which ``tests/arena/conftest.py`` clears around every test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings as arena_settings
from shared.db_schema import security_events
from shared.db_schema.arena.arena_submissions import arena_submission_judgments, arena_submissions
from shared.enumerations import ArenaRole
from tests.arena._admin_problem_app import build_admin_app, create_user, login_token
from tests.arena.test_admin_problem_judgment import _make_problem
from tests.arena.test_admin_submissions_route import _insert_judgment, _insert_submission, _make_language

pytestmark = pytest.mark.asyncio

_PASSWORD = "StrongPass1!"


def _throttle(monkeypatch: pytest.MonkeyPatch, *, account_max: int = 2) -> None:
    target = "arena.routes.auth_common.settings"
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_WINDOW_SECONDS", 900)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES", account_max)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_IP_MAX_FAILURES", 100)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_LOCKOUT_SECONDS", 900)


async def _setup(session: AsyncSession, monkeypatch: pytest.MonkeyPatch, *, email: str):  # type: ignore[no-untyped-def]
    """Return a client, its problem id, and the enqueue mock, with two settled submissions."""
    app = build_admin_app(session)
    judge = await create_user(session, email=email, role=ArenaRole.ARENA_JUDGE, can_edit=True)
    judge.password = _PASSWORD
    await session.commit()
    problem_id = await _make_problem(session, judge.id)
    language = await _make_language(session)
    for verdict in ("AC", "WA"):
        submission_id = await _insert_submission(session, judge.id, problem_id, language.id)
        await _insert_judgment(session, submission_id, final_verdict=verdict, status="DONE")
    await session.commit()

    enqueue = AsyncMock()
    monkeypatch.setattr("arena.routes.admin_problems.enqueue_arena_submission_job", enqueue)
    client = AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 12345)),
        base_url="http://testserver",
        cookies={"arena_access_token": login_token(app, judge)},
        follow_redirects=False,
    )
    return client, problem_id, judge.id, language.id, enqueue


async def _status_counts(session: AsyncSession, problem_id: str) -> dict[str, int]:
    rows = (
        await session.execute(
            select(arena_submission_judgments.c.status, func.count())
            .select_from(arena_submission_judgments.join(arena_submissions))
            .where(arena_submissions.c.problem_id == problem_id)
            .group_by(arena_submission_judgments.c.status)
        )
    ).all()
    return {status: count for status, count in rows}


async def _events(session: AsyncSession, event_type: str) -> list[dict[str, object]]:
    rows = (
        await session.execute(
            select(security_events.c.severity, security_events.c["metadata"]).where(
                security_events.c.event_type == event_type
            )
        )
    ).all()
    return [{"severity": r[0], "metadata": r[1]} for r in rows]


async def test_wrong_password_is_counted_and_the_cap_locks_the_route(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _throttle(monkeypatch, account_max=2)
    client, problem_id, _, _, enqueue = await _setup(session, monkeypatch, email="rejudge-oracle@test.example")
    url = f"/admin/problems/{problem_id}/rejudge-all"

    async with client:
        first = await client.post(url, data={"password": "wrong"})
        second = await client.post(url, data={"password": "wrong"})
        locked = await client.post(url, data={"password": _PASSWORD})

    assert first.status_code == 303 and second.status_code == 303
    assert locked.status_code == 429 and "Retry-After" in locked.headers
    enqueue.assert_not_awaited()
    assert (await _status_counts(session, problem_id)) == {"DONE": 2}
    assert len(await _events(session, "auth_failure")) == 2
    assert await _events(session, "auth_throttle_lockout")
    assert not await _events(session, "admin_action")


async def test_settled_submissions_are_queued_in_flight_ones_are_skipped_and_audited(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(arena_settings, "REJUDGE_COOLDOWN_SECONDS", 0)
    client, problem_id, judge_id, language_id, enqueue = await _setup(
        session, monkeypatch, email="rejudge-skip@test.example"
    )
    running = await _insert_submission(session, judge_id, problem_id, language_id)
    await _insert_judgment(session, running, final_verdict=None, status="JUDGING")
    await session.commit()

    async with client:
        response = await client.post(f"/admin/problems/{problem_id}/rejudge-all", data={"password": _PASSWORD})

    assert response.status_code == 303
    assert enqueue.await_count == 2
    assert {call.args[1].submission_id for call in enqueue.await_args_list}.isdisjoint({running})
    # Two superseded + two new queued for the settled ones; the in-flight one untouched.
    assert (await _status_counts(session, problem_id)) == {"SUPERSEDED": 2, "QUEUED": 2, "JUDGING": 1}

    audits = await _events(session, "admin_action")
    assert len(audits) == 1
    assert audits[0]["severity"] == "warning"
    assert audits[0]["metadata"]["action"] == "rejudge_all"  # type: ignore[index]
    assert audits[0]["metadata"]["target_id"] == problem_id  # type: ignore[index]
    assert audits[0]["metadata"]["detail"] == "queued=2 skipped_in_flight=1"  # type: ignore[index]


async def test_a_repeat_queues_nothing_new(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arena_settings, "REJUDGE_COOLDOWN_SECONDS", 0)
    client, problem_id, _, _, enqueue = await _setup(session, monkeypatch, email="rejudge-repeat@test.example")
    url = f"/admin/problems/{problem_id}/rejudge-all"

    async with client:
        await client.post(url, data={"password": _PASSWORD})
        await client.post(url, data={"password": _PASSWORD})

    assert enqueue.await_count == 2
    assert (await _status_counts(session, problem_id)) == {"SUPERSEDED": 2, "QUEUED": 2}
    assert len(await _events(session, "admin_action")) == 1


async def test_the_cooldown_refuses_a_repeat_before_the_selection_runs(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(arena_settings, "REJUDGE_COOLDOWN_SECONDS", 300)
    client, problem_id, _, _, enqueue = await _setup(session, monkeypatch, email="rejudge-cooldown@test.example")
    url = f"/admin/problems/{problem_id}/rejudge-all"
    build = AsyncMock(side_effect=AssertionError("selection must not run under the cooldown"))

    async with client:
        first = await client.post(url, data={"password": _PASSWORD})
        monkeypatch.setattr("arena.routes.admin_problems.rejudge_service.build_rejudge_jobs", build)
        repeat = await client.post(url, data={"password": _PASSWORD})

    assert first.status_code == 303 and repeat.status_code == 303
    assert enqueue.await_count == 2
    build.assert_not_awaited()
