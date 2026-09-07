#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``GET /c/{slug}/admin/problems/import/sample`` serves the memoized package (#157)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services import sample_problem_package as module
from shared.services.sample_problem_package import SAMPLE_PACKAGE_FILENAME, clear_sample_problem_package_memo
from tests.web.test_contest_admin_problem_chooser import _build_app
from web.models.contest import Contest
from web.models.users import UberAdmin


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    clear_sample_problem_package_memo()
    yield
    clear_sample_problem_package_memo()


@pytest_asyncio.fixture
async def upcoming_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    contest = Contest(
        contest_name="Sample Package Contest",
        contest_url="http://sample.example.com",
        login_slug="sample-package-contest",
        start_time=datetime.now(UTC) + timedelta(hours=2),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


@pytest_asyncio.fixture
async def client(session: AsyncSession, upcoming_contest: Contest, uberadmin: UberAdmin) -> AsyncClient:
    app = _build_app(session, upcoming_contest, uberadmin)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False)


@pytest.mark.asyncio
async def test_sample_package_is_built_once_and_revalidates_by_etag(
    client: AsyncClient, upcoming_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two downloads cost one build; a third with the tag costs no body at all."""
    calls = 0
    real = module.build_sample_problem_package

    def counting(destination: Path) -> Path:
        nonlocal calls
        calls += 1
        return real(destination)

    monkeypatch.setattr(module, "build_sample_problem_package", counting)
    url = f"/c/{upcoming_contest.login_slug}/admin/problems/import/sample"

    first = await client.get(url)
    second = await client.get(url)

    assert first.status_code == second.status_code == 200
    assert calls == 1
    assert first.content == second.content
    assert first.content[:2] == b"PK"
    assert first.headers["content-type"] == "application/zip"
    assert first.headers["content-disposition"] == f'attachment; filename="{SAMPLE_PACKAGE_FILENAME}"'
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.headers["etag"].startswith('"')

    revalidated = await client.get(url, headers={"If-None-Match": first.headers["etag"]})

    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["etag"] == first.headers["etag"]
    assert calls == 1
