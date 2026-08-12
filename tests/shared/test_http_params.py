#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the bounded request-parameter types.

These pin the behaviour behind a production incident: an unbounded ``int`` route
parameter reached PostgreSQL, overflowed ``int32``, and surfaced as a 503 with a
full SQL statement in the log.  Bounding the parameter turns that into an
ordinary rejected request that never opens a database connection.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.http_params import MAX_PAGE, PG_INT32_MAX, DbId, PageNumber
from shared.services.pagination_service import parse_page

pytestmark = pytest.mark.asyncio


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/problems/{arena_number}")
    async def problem_detail(arena_number: DbId) -> dict[str, int]:
        return {"arena_number": arena_number}

    @app.get("/problems")
    async def problem_list(page: PageNumber = 1) -> dict[str, int]:
        return {"page": page}

    return app


@pytest.mark.parametrize(
    "value",
    [
        # The exact value from the production log.
        "99999999999999999",
        str(PG_INT32_MAX + 1),
        "0",
        "-1",
        "abc",
    ],
)
async def test_out_of_range_path_id_is_refused_before_the_database(value: str) -> None:
    """No out-of-range identifier may reach a query."""
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/problems/{value}")

    # 422 from the bound; never a 503, which is what the overflow produced.
    assert response.status_code == 422
    assert response.status_code != 503


async def test_largest_valid_identifier_is_still_accepted() -> None:
    """The bound is the column's domain, not an arbitrary cut."""
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/problems/{PG_INT32_MAX}")

    assert response.status_code == 200
    assert response.json() == {"arena_number": PG_INT32_MAX}


@pytest.mark.parametrize("value", ["999999999999999999999999", "0", "abc"])
async def test_out_of_range_page_is_refused(value: str) -> None:
    """The reported `?page=` overflow is rejected rather than run as an OFFSET."""
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/problems?page={value}")

    assert response.status_code == 422


async def test_parse_page_clamps_rather_than_refusing() -> None:
    """Routes taking a forgiving string page stay forgiving, but bounded."""
    assert parse_page("0") == 1
    assert parse_page("abc") == 1
    assert parse_page(None) == 1
    assert parse_page("7") == 7
    # The important half: no longer unbounded above.
    assert parse_page("999999999999999999999999") == MAX_PAGE
    assert parse_page(MAX_PAGE + 1) == MAX_PAGE


async def test_page_bound_keeps_offset_well_inside_int32() -> None:
    """A page at the cap must not be able to overflow the SQL OFFSET either."""
    largest_per_page = 100
    assert MAX_PAGE * largest_per_page < PG_INT32_MAX
