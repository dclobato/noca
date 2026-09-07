#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The per-user budgets on Arena's admin exports and teacher reports (#157).

Two surfaces, two buckets. The structural tests pin which route carries which
budget; the behavioural ones prove a budget refuses with ``429`` and that a
teacher's report browsing and an administrator's downloads never share a count.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from _admin_problem_app import build_admin_app, create_user, login_token
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from arena.dependencies import export_rate_limit
from arena.dependencies.export_rate_limit import (
    ADMIN_EXPORT_BUCKET,
    EXPORT_DETAIL,
    TEACHER_REPORT_BUCKET,
    arena_admin_export_rate_limit,
    arena_teacher_report_rate_limit,
)
from arena.dependencies.problem_export_rate_limit import PROBLEM_EXPORT_BUCKET
from arena.dependencies.user_read_rate_limit import USER_READ_BUCKET
from arena.routes import (
    admin_dashboard_security,
    admin_problem_io,
    problem_sets_full_report,
    problem_sets_report,
)
from shared.enumerations import ArenaRole

Guard = Callable[..., Awaitable[None]]

_EXPECTED: tuple[tuple[Any, str, Guard], ...] = (
    (admin_problem_io.router, "arena_admin_problem_export", arena_admin_export_rate_limit),
    (admin_dashboard_security.router, "arena_admin_dashboard_security_events_csv", arena_admin_export_rate_limit),
    (problem_sets_full_report.router, "arena_class_full_report", arena_teacher_report_rate_limit),
    (problem_sets_full_report.router, "arena_class_full_report_csv", arena_teacher_report_rate_limit),
    (problem_sets_report.router, "arena_class_problem_set_report", arena_teacher_report_rate_limit),
    (problem_sets_report.router, "arena_class_problem_set_report_student", arena_teacher_report_rate_limit),
)


def _route(router: Any, name: str) -> APIRoute:
    matches = [r for r in router.routes if isinstance(r, APIRoute) and r.name == name]
    assert len(matches) == 1, f"{name}: {len(matches)} routes"
    return matches[0]


def _guards(route: APIRoute) -> set[object]:
    return {dependency.dependency for dependency in route.dependencies}


@pytest.mark.parametrize(("router", "name", "guard"), _EXPECTED, ids=[e[1] for e in _EXPECTED])
def test_every_heavy_route_carries_its_surface_budget(router: Any, name: str, guard: Guard) -> None:
    guards = _guards(_route(router, name))
    assert guard in guards
    other = {arena_admin_export_rate_limit, arena_teacher_report_rate_limit} - {guard}
    assert not (guards & other), f"{name} carries the other surface's budget"


def test_the_buckets_are_all_distinct() -> None:
    buckets = [ADMIN_EXPORT_BUCKET, TEACHER_REPORT_BUCKET, PROBLEM_EXPORT_BUCKET, USER_READ_BUCKET]
    assert len(set(buckets)) == len(buckets)


def _set_budget(monkeypatch: pytest.MonkeyPatch, prefix: str, *, max_requests: int, enabled: bool = True) -> None:
    settings = export_rate_limit.settings
    monkeypatch.setattr(settings, f"{prefix}_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(settings, f"{prefix}_RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(settings, f"{prefix}_RATE_LIMIT_WINDOW_SECONDS", 600)


async def _admin_client(session: AsyncSession, *, email: str) -> AsyncClient:
    """An Arena admin client over the admin app plus the security-events router."""
    app = build_admin_app(session)
    app.include_router(admin_dashboard_security.router)
    admin = await create_user(session, email=email, role=ArenaRole.ARENA_ADMIN, can_edit=True)
    return AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 12345)),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": login_token(app, admin)},
    )


@pytest.mark.asyncio
async def test_admin_export_budget_answers_429_with_retry_after(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The security CSV and the problem export share the admin bucket; the third call is refused."""
    _set_budget(monkeypatch, "ADMIN_EXPORT", max_requests=2)
    client = await _admin_client(session, email="export-admin@test.example")

    first = await client.get("/admin/dashboard/security-events.csv")
    second = await client.get("/admin/problems/no-such-problem/export")
    third = await client.get("/admin/dashboard/security-events.csv")

    assert first.status_code == 200
    # The 404 for an unknown problem still spent one download: the budget is charged first.
    assert second.status_code == 404
    assert third.status_code == 429
    assert third.json() == {"detail": EXPORT_DETAIL}
    assert int(third.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_the_teacher_bucket_is_untouched_by_admin_downloads(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spending the admin budget leaves the teacher-report counter empty."""
    _set_budget(monkeypatch, "ADMIN_EXPORT", max_requests=1)
    client = await _admin_client(session, email="export-admin-2@test.example")

    assert (await client.get("/admin/dashboard/security-events.csv")).status_code == 200
    assert (await client.get("/admin/dashboard/security-events.csv")).status_code == 429

    assert export_rate_limit.ADMIN_EXPORT_LIMITER._buckets
    assert not export_rate_limit.TEACHER_REPORT_LIMITER._buckets


@pytest.mark.asyncio
async def test_disabling_the_knob_removes_the_budget(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_budget(monkeypatch, "ADMIN_EXPORT", max_requests=1, enabled=False)
    client = await _admin_client(session, email="export-admin-3@test.example")

    statuses = [(await client.get("/admin/dashboard/security-events.csv")).status_code for _ in range(3)]

    assert statuses == [200, 200, 200]
