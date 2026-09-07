#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The per-actor budgets on Web's heavy exports and reports (#157).

Four surfaces, four buckets. The structural tests pin which route carries which
budget, because each is a ``Depends`` declaration a later edit could silently
drop; the behavioural tests prove a budget refuses, that the buckets are really
separate, and that the actor key tells an UberAdmin, a contest admin, and the
same login in another contest apart.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from jwtservice.core import TokenVerificationResult
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.database import get_db
from web.dependencies import ContestAdminContext, ContestContext, get_contest_admin_context, get_contest_context
from web.dependencies import get_uberadmin as get_uberadmin_dependency
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.routes import (
    contest_admin_export,
    contest_admin_problem_io,
    contest_admin_user_edit,
    contest_reports,
    contest_submissions,
    uberadmin_security,
)
from web.services import export_rate_limit
from web.services.export_rate_limit import (
    ADMIN_EXPORT_BUCKET,
    CONTEST_REPORT_BUCKET,
    EXPORT_DETAIL,
    TEAM_DOWNLOAD_BUCKET,
    UBERADMIN_EXPORT_BUCKET,
    web_admin_export_rate_limit,
    web_contest_report_rate_limit,
    web_team_download_rate_limit,
    web_uberadmin_export_rate_limit,
)
from web.services.problem_export_rate_limit import PROBLEM_EXPORT_BUCKET
from web.services.user_read_rate_limit import USER_READ_BUCKET, web_actor_key

IP_A = "203.0.113.10"

Guard = Callable[..., Awaitable[None]]

#: (router, route name, budget) for every route the audit named.
_EXPECTED: tuple[tuple[Any, str, Guard], ...] = (
    (contest_admin_problem_io.router, "export_problem", web_admin_export_rate_limit),
    (contest_admin_export.router, "export_animeitor", web_admin_export_rate_limit),
    (contest_admin_export.router, "export_contest_timeline", web_admin_export_rate_limit),
    (contest_admin_export.router, "users_per_site_report", web_admin_export_rate_limit),
    (contest_admin_user_edit.router, "export_users", web_admin_export_rate_limit),
    (contest_reports.router, "contest_reports", web_contest_report_rate_limit),
    (contest_submissions.router, "team_submissions_download", web_team_download_rate_limit),
    (uberadmin_security.router, "uberadmin_security_events_csv", web_uberadmin_export_rate_limit),
)


def _route(router: Any, name: str) -> APIRoute:
    matches = [r for r in router.routes if isinstance(r, APIRoute) and r.name == name]
    assert len(matches) == 1, f"{name}: {len(matches)} routes"
    return matches[0]


def _guards(route: APIRoute) -> set[object]:
    return {dependency.dependency for dependency in route.dependencies}


# ── Structure ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("router", "name", "guard"), _EXPECTED, ids=[e[1] for e in _EXPECTED])
def test_every_heavy_route_carries_its_surface_budget(router: Any, name: str, guard: Guard) -> None:
    """Each route named by the audit carries exactly the budget of its surface."""
    guards = _guards(_route(router, name))
    assert guard in guards
    others = {
        web_admin_export_rate_limit,
        web_contest_report_rate_limit,
        web_team_download_rate_limit,
        web_uberadmin_export_rate_limit,
    } - {guard}
    assert not (guards & others), f"{name} carries a second surface's budget"


def test_the_buckets_are_all_distinct() -> None:
    """Sharing any two would let one surface's traffic spend another's allowance."""
    buckets = [
        ADMIN_EXPORT_BUCKET,
        CONTEST_REPORT_BUCKET,
        TEAM_DOWNLOAD_BUCKET,
        UBERADMIN_EXPORT_BUCKET,
        PROBLEM_EXPORT_BUCKET,
        USER_READ_BUCKET,
    ]
    assert len(set(buckets)) == len(buckets)


# ── The actor key ────────────────────────────────────────────────────────────


class _Stamped:
    """Pure-ASGI middleware planting a validated token, as the auth middleware would."""

    def __init__(self, app: Any, token: TokenVerificationResult) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["validated_token"] = self._token
        await self._app(scope, receive, send)


def _token(*, sub: str, aud: str, contest_id: str | None = None) -> TokenVerificationResult:
    extra = {"contest_id": contest_id} if contest_id else None
    return TokenVerificationResult(valid=True, status="valid", sub=sub, aud=aud, extra_data=extra)


def _request_with(token: TokenVerificationResult | None) -> Any:
    from fastapi import Request

    state: dict[str, Any] = {"validated_token": token} if token is not None else {}
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "state": state})


def test_actor_key_carries_domain_and_contest() -> None:
    """Three people who share the login `admin` get three keys."""
    uber = web_actor_key(_request_with(_token(sub="admin", aud=RoleEnum.UBERADMIN.value)))
    contest_one = web_actor_key(_request_with(_token(sub="admin", aud=RoleEnum.ADMIN.value, contest_id="c1")))
    contest_two = web_actor_key(_request_with(_token(sub="admin", aud=RoleEnum.ADMIN.value, contest_id="c2")))

    assert uber == f"{RoleEnum.UBERADMIN.value}:admin"
    assert contest_one == f"{RoleEnum.ADMIN.value}:c1:admin"
    assert len({uber, contest_one, contest_two}) == 3


def test_actor_key_is_none_without_a_session() -> None:
    """An anonymous request falls back to the shared per-IP key, as before."""
    assert web_actor_key(_request_with(None)) is None
    assert web_actor_key(_request_with(TokenVerificationResult(valid=False, status="expired", sub="x"))) is None


# ── Behaviour ────────────────────────────────────────────────────────────────


def _set_budget(monkeypatch: pytest.MonkeyPatch, prefix: str, *, max_requests: int, enabled: bool = True) -> None:
    settings = export_rate_limit.settings
    monkeypatch.setattr(settings, f"{prefix}_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(settings, f"{prefix}_RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(settings, f"{prefix}_RATE_LIMIT_WINDOW_SECONDS", 600)


def _admin_app(session: AsyncSession, contest: Contest, actor: UberAdmin | User, token: TokenVerificationResult) -> Any:
    app = FastAPI()
    app.include_router(contest_admin_user_edit.router)
    app.include_router(contest_submissions.router)

    async def _admin_ctx() -> ContestAdminContext:
        return ContestAdminContext(contest=contest, session=session, actor=actor)

    async def _ctx() -> ContestContext:
        return ContestContext(contest=contest, session=session, actor=actor)

    app.dependency_overrides[get_contest_admin_context] = _admin_ctx
    app.dependency_overrides[get_contest_context] = _ctx
    return _Stamped(app, token)


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(IP_A, 12345)), base_url="http://testserver")


@pytest.mark.asyncio
async def test_admin_export_budget_answers_429_with_retry_after(
    session: AsyncSession, running_contest: Contest, admin_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third users export in a two-download window is refused."""
    _set_budget(monkeypatch, "ADMIN_EXPORT", max_requests=2)
    token = _token(sub=admin_user.username, aud=RoleEnum.ADMIN.value, contest_id=running_contest.id)
    url = f"/c/{running_contest.login_slug}/admin/users/export.json"

    async with _client(_admin_app(session, running_contest, admin_user, token)) as client:
        statuses = [(await client.get(url)).status_code for _ in range(2)]
        third = await client.get(url)

    assert statuses == [200, 200]
    assert third.status_code == 429
    assert third.json() == {"detail": EXPORT_DETAIL}
    assert int(third.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_the_same_login_in_another_contest_has_its_own_budget(
    session: AsyncSession, running_contest: Contest, admin_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spent budget in contest A leaves `admin` in contest B untouched."""
    _set_budget(monkeypatch, "ADMIN_EXPORT", max_requests=1)
    url = f"/c/{running_contest.login_slug}/admin/users/export.json"
    token_a = _token(sub=admin_user.username, aud=RoleEnum.ADMIN.value, contest_id=running_contest.id)
    token_b = _token(sub=admin_user.username, aud=RoleEnum.ADMIN.value, contest_id="another-contest")

    async with _client(_admin_app(session, running_contest, admin_user, token_a)) as client:
        assert (await client.get(url)).status_code == 200
        assert (await client.get(url)).status_code == 429
    async with _client(_admin_app(session, running_contest, admin_user, token_b)) as client:
        assert (await client.get(url)).status_code == 200


@pytest.mark.asyncio
async def test_team_download_is_charged_even_when_the_route_then_refuses(
    session: AsyncSession, running_contest: Contest, team_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running contest answers 403 to download-all, and those 403s still spend the budget."""
    _set_budget(monkeypatch, "TEAM_DOWNLOAD", max_requests=2)
    token = _token(sub=team_user.username, aud=RoleEnum.TEAM.value, contest_id=running_contest.id)
    url = f"/c/{running_contest.login_slug}/submissions/download-all"

    async with _client(_admin_app(session, running_contest, team_user, token)) as client:
        statuses = [(await client.get(url)).status_code for _ in range(3)]

    assert statuses == [403, 403, 429]


@pytest.mark.asyncio
async def test_uberadmin_csv_budget_is_separate_from_the_admin_export_budget(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spending the UberAdmin CSV budget refuses the CSV and nothing else."""
    _set_budget(monkeypatch, "UBERADMIN_EXPORT", max_requests=1)
    _set_budget(monkeypatch, "ADMIN_EXPORT", max_requests=1)
    app = FastAPI()
    app.include_router(uberadmin_security.router)

    async def _uber() -> UberAdmin:
        return uberadmin

    async def _db():
        yield session

    app.dependency_overrides[get_uberadmin_dependency] = _uber
    app.dependency_overrides[get_db] = _db
    stamped = _Stamped(app, _token(sub=uberadmin.username, aud=RoleEnum.UBERADMIN.value))

    async with _client(stamped) as client:
        first = await client.get("/uberadmin/security-events.csv")
        second = await client.get("/uberadmin/security-events.csv")

    assert first.status_code == 200
    assert second.status_code == 429
    # The admin-export bucket never saw the UberAdmin's requests.
    assert not export_rate_limit.ADMIN_EXPORT_LIMITER._buckets
    assert export_rate_limit.UBERADMIN_EXPORT_LIMITER._buckets


@pytest.mark.asyncio
async def test_disabling_the_knob_removes_the_budget(
    session: AsyncSession, running_contest: Contest, admin_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_budget(monkeypatch, "ADMIN_EXPORT", max_requests=1, enabled=False)
    token = _token(sub=admin_user.username, aud=RoleEnum.ADMIN.value, contest_id=running_contest.id)
    url = f"/c/{running_contest.login_slug}/admin/users/export.json"

    async with _client(_admin_app(session, running_contest, admin_user, token)) as client:
        statuses = [(await client.get(url)).status_code for _ in range(3)]

    assert statuses == [200, 200, 200]
