#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The two limit-change-batch rejudge routes after issue #156.

Contract: both routes reconfirm the admin password through the shared
``password-confirm`` budget (a wrong password queues nothing and is recorded;
past the cap even the correct password is refused with the ``429`` page); the
batch-wide route holds a per-problem cooldown that the per-language route does
not; an accepted action writes one warning-severity ``admin_action`` row in the
same transaction as the ``QUEUED`` judgments; and a repeat queues nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import valkey.asyncio as aivalkey
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.enumerations import JudgmentStatus, Verdict
from tests.web._contest_admin_test_support import actor_token, admin_on, build_contest_admin_app
from tests.web.test_judging_service import _make_language, _make_submission_with_judgment
from web.config import settings
from web.models.contest import Contest
from web.models.problem import Problem, ProblemLimitChangeBatchSubmission
from web.models.users import UberAdmin, User
from web.routes.contest_admin_problem_limits import router as limits_router
from web.services.problem_service import (
    changed_effective_limits,
    create_problem_limit_change_batch,
    problem_fallback_limits,
)

pytestmark = pytest.mark.asyncio

GOOD = "TestPass1!"
IP = ("203.0.113.10", 12345)


class _Runtime:
    """A raw Valkey client behind the attributes the route reads off the runtime."""

    is_available = True

    def __init__(self, client: aivalkey.Valkey) -> None:
        self._client = client

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


def _cap(monkeypatch: pytest.MonkeyPatch, *, account: int = 2) -> None:
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES", account)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_IP_MAX_FAILURES", 20)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 900)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_LOCKOUT_SECONDS", 900)


async def _batch_with_pending_rows(
    session: AsyncSession, contest: Contest, problem: Problem, admin: User, team: User, *, rows: int
) -> str:
    """Persist a limit-change batch holding ``rows`` pending TLE submissions and return its id."""
    language = await _make_language(session)
    for _ in range(rows):
        await _make_submission_with_judgment(
            session,
            problem=problem,
            team=team,
            language=language,
            status=JudgmentStatus.DONE,
            autojudge_verdict=Verdict.TLE,
            final_verdict=Verdict.TLE,
        )
    changed = changed_effective_limits(
        problem,
        [language],
        before_overrides={},
        after_overrides={
            language.id: {
                "time_limit_ms": problem.time_limit_ms + 500,
                "memory_limit_kb": problem.memory_limit_kb,
                "pids_limit": problem.pids_limit,
                "output_limit_in_bytes": "",
                "repetitions": 1,
            }
        },
        before_fallback=problem_fallback_limits(problem),
        after_fallback=problem_fallback_limits(problem),
    )
    batch = await create_problem_limit_change_batch(session, contest, problem, admin, changed)
    assert batch is not None
    return batch.id


async def _setup(  # type: ignore[no-untyped-def]
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    contest_problem: Problem,
    team_user: User,
    valkey_client: aivalkey.Valkey,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: int = 2,
):
    admin = await admin_on(session, running_contest, uberadmin, "batch-admin")
    batch_id = await _batch_with_pending_rows(session, running_contest, contest_problem, admin, team_user, rows=rows)
    await session.commit()  # the app opens its own sessions and sees only committed rows

    app, auth_service = build_contest_admin_app(session, routers=(limits_router,), extra_scoped_stub_names=("view",))
    # The batch page links to these two with extra path params the generic stubs lack.
    app.add_api_route("/stub/edit/{slug}/{problem_id}", _stub_two, name="edit_problem_form", methods=["GET"])
    app.add_api_route("/stub/review/{slug}/{submission_id}", _stub_two, name="submission_review", methods=["GET"])
    app.state.valkey_runtime = _Runtime(valkey_client)
    enqueue = AsyncMock()
    invalidate = AsyncMock()
    monkeypatch.setattr("web.routes.contest_admin_problem_limits.enqueue_job", enqueue)
    monkeypatch.setattr("web.routes.contest_admin_problem_limits.invalidate_scoreboard_cache", invalidate)
    token = actor_token(auth_service, username=admin.username, contest_id=running_contest.id)
    base = f"/c/{running_contest.login_slug}/admin/problems/{contest_problem.id}/limit-change-batches/{batch_id}"
    return app, token, base, enqueue, invalidate


async def _stub_two(slug: str, problem_id: str = "", submission_id: str = "") -> dict[str, str]:
    return {"slug": slug}


def _client(app) -> AsyncClient:  # type: ignore[no-untyped-def]
    return AsyncClient(transport=ASGITransport(app=app, client=IP), base_url="http://testserver")


async def _events(session: AsyncSession, event_type: str) -> list[dict[str, object]]:
    rows = (
        await session.execute(
            select(security_events.c.severity, security_events.c["metadata"]).where(
                security_events.c.event_type == event_type
            )
        )
    ).all()
    return [{"severity": r[0], "metadata": r[1]} for r in rows]


async def _row_statuses(session: AsyncSession) -> list[str]:
    session.expire_all()
    rows = (await session.execute(select(ProblemLimitChangeBatchSubmission.rejudge_status))).scalars().all()
    return sorted(rows)


async def test_review_page_renders_the_password_modal_instead_of_bare_forms(
    session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    app, token, base, _, _ = await _setup(
        session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
    )
    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        page = await client.get(base)

    assert page.status_code == 200
    assert 'id="limit-rejudge-modal"' in page.text
    assert 'name="password"' in page.text
    assert page.text.count("data-limit-rejudge-button") == 2
    assert "limit-batch-rejudge.js" in page.text


async def test_wrong_password_queues_nothing_and_is_recorded(
    session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    _cap(monkeypatch)
    app, token, base, enqueue, _ = await _setup(
        session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
    )
    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(f"{base}/rejudge-all", data={"password": "wrong"})

    assert response.status_code == 303 and response.headers["location"].endswith(base)
    enqueue.assert_not_awaited()
    assert await _row_statuses(session) == ["PENDING", "PENDING"]
    failures = await _events(session, "auth_failure")
    assert len(failures) == 1
    assert failures[0]["metadata"]["route"] == "limit_batch_rejudge_all"  # type: ignore[index]
    assert not await _events(session, "admin_action")


async def test_lockout_refuses_even_the_correct_password(
    session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    _cap(monkeypatch, account=2)
    app, token, base, enqueue, _ = await _setup(
        session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
    )
    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        await client.post(f"{base}/rejudge-all", data={"password": "wrong"})
        await client.post(f"{base}/rejudge-all", data={"password": "wrong"})
        locked = await client.post(f"{base}/rejudge-all", data={"password": GOOD})

    assert locked.status_code == 429 and "Retry-After" in locked.headers
    assert "Too many failed password confirmations" in locked.text
    enqueue.assert_not_awaited()
    assert await _row_statuses(session) == ["PENDING", "PENDING"]
    assert await _events(session, "auth_throttle_lockout")


async def test_happy_path_queues_audits_and_a_repeat_hits_the_cooldown(
    session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "REJUDGE_COOLDOWN_SECONDS", 300)
    problem_id = contest_problem.id
    app, token, base, enqueue, invalidate = await _setup(
        session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
    )
    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        first = await client.post(f"{base}/rejudge-all", data={"password": GOOD})
        repeat = await client.post(f"{base}/rejudge-all", data={"password": GOOD})
        review = await client.get(base)

    assert first.status_code == 303 and repeat.status_code == 303
    assert enqueue.await_count == 2
    assert all(call.kwargs["priority"] is True for call in enqueue.await_args_list)
    invalidate.assert_awaited_once()
    assert await _row_statuses(session) == ["QUEUED", "QUEUED"]

    audits = await _events(session, "admin_action")
    assert len(audits) == 1
    assert audits[0]["severity"] == "warning"
    assert audits[0]["metadata"]["action"] == "limit_batch_rejudge_all"  # type: ignore[index]
    assert audits[0]["metadata"]["target_id"] == problem_id  # type: ignore[index]
    assert "queued=2" in audits[0]["metadata"]["detail"]  # type: ignore[index]
    # The repeat was refused by the cooldown before touching the batch, and says so.
    assert "started less than 300 s ago" in review.text


async def test_with_the_cooldown_disabled_a_repeat_simply_finds_nothing_pending(
    session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "REJUDGE_COOLDOWN_SECONDS", 0)
    app, token, base, enqueue, _ = await _setup(
        session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
    )
    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        await client.post(f"{base}/rejudge-all", data={"password": GOOD})
        await client.post(f"{base}/rejudge-all", data={"password": GOOD})
        review = await client.get(base)

    assert enqueue.await_count == 2
    assert "No pending submissions were eligible" in review.text
    assert len(await _events(session, "admin_action")) == 1


async def test_language_route_confirms_and_audits_but_ignores_the_cooldown(
    session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "REJUDGE_COOLDOWN_SECONDS", 300)
    app, token, base, enqueue, _ = await _setup(
        session, running_contest, uberadmin, contest_problem, team_user, valkey_client, monkeypatch, rows=1
    )
    language_url = f"{base}/languages/python3/rejudge"
    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        wrong = await client.post(language_url, data={"password": "wrong"})
        # A batch-wide action opened the cooldown window on this problem...
        await client.post(f"{base}/rejudge-all", data={"password": GOOD})
        # ...which the per-language route does not consult: it runs and finds nothing pending.
        again = await client.post(language_url, data={"password": GOOD})
        review = await client.get(base)

    assert wrong.status_code == 303 and again.status_code == 303
    assert enqueue.await_count == 1
    assert "started less than" not in review.text
    assert "No pending submissions were eligible" in review.text
    failures = await _events(session, "auth_failure")
    assert [f["metadata"]["route"] for f in failures] == ["limit_batch_rejudge_language"]  # type: ignore[index]
    audits = await _events(session, "admin_action")
    assert [a["metadata"]["action"] for a in audits] == ["limit_batch_rejudge_all"]  # type: ignore[index]
