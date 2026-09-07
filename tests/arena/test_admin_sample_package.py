#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``GET /admin/problems/import/sample`` serves the memoized package (#157)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from _admin_problem_app import build_admin_app, create_user, login_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ArenaRole
from shared.services import sample_problem_package as module
from shared.services.sample_problem_package import SAMPLE_PACKAGE_FILENAME, clear_sample_problem_package_memo

URL = "/admin/problems/import/sample"


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    clear_sample_problem_package_memo()
    yield
    clear_sample_problem_package_memo()


async def _client(session: AsyncSession, *, email: str, role: ArenaRole, can_edit: bool) -> AsyncClient:
    app = build_admin_app(session)
    user = await create_user(session, email=email, role=role, can_edit=can_edit)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": login_token(app, user)},
    )


@pytest.mark.asyncio
async def test_sample_package_is_built_once_and_revalidates_by_etag(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two downloads cost one build; a third with the tag costs no body at all."""
    calls = 0
    real = module.build_sample_problem_package

    def counting(destination: Path) -> Path:
        nonlocal calls
        calls += 1
        return real(destination)

    monkeypatch.setattr(module, "build_sample_problem_package", counting)
    client = await _client(session, email="sample-editor@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)

    first = await client.get(URL)
    second = await client.get(URL)

    assert first.status_code == second.status_code == 200
    assert calls == 1
    assert first.content == second.content
    assert first.content[:2] == b"PK"
    assert first.headers["content-type"] == "application/zip"
    assert first.headers["content-disposition"] == f'attachment; filename="{SAMPLE_PACKAGE_FILENAME}"'
    assert first.headers["cache-control"] == "private, no-cache"

    revalidated = await client.get(URL, headers={"If-None-Match": first.headers["etag"]})

    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["etag"] == first.headers["etag"]
    assert calls == 1


@pytest.mark.asyncio
async def test_sample_package_still_requires_a_problem_editor(session: AsyncSession) -> None:
    """Memoizing the bytes must not loosen the gate in front of them."""
    client = await _client(session, email="sample-user@test.example", role=ArenaRole.ARENA_USER, can_edit=False)

    response = await client.get(URL)

    assert response.status_code in (302, 303, 403)
