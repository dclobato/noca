#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the generation-keyed contest report cache."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.contest_report_cache import (
    PAYLOAD_VERSION,
    contest_report_data_key,
    contest_report_generation_key,
    invalidate_contest_report_cache,
)
from web.models.contest import Contest
from web.services import contest_report_cache
from web.services.contest_report_cache import (
    REPORT_CACHE_TTL_SECONDS,
    ContestReportPageData,
    get_contest_report_page_data,
)


class _FakeValkey:
    """Minimal string store that records report-cache writes."""

    is_available = True

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.writes: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        """Return a stored string or a cache miss."""
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        """Store a string and record its expiry."""
        self.values[key] = value
        self.writes.append((key, value, ex))


@pytest.fixture(autouse=True)
def _clear_local_cache() -> None:
    """Keep the process-local cache isolated between tests."""
    contest_report_cache._local_cache.clear()


def _page_data(marker: str) -> ContestReportPageData:
    """Build a small payload whose origin is easy to assert."""
    return ContestReportPageData(report={"marker": marker}, chart_data={"series": [marker]})


@pytest.mark.asyncio
async def test_repeated_reads_compute_once_and_write_ten_minute_ttl(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    running_contest: Contest,
) -> None:
    valkey = _FakeValkey()
    calls = 0

    async def compute(*_args: Any, **_kwargs: Any) -> ContestReportPageData:
        nonlocal calls
        calls += 1
        return _page_data("fresh")

    monkeypatch.setattr(contest_report_cache, "_compute_page_data", compute)

    first = await get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)
    second = await get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)

    assert first == second == _page_data("fresh")
    assert calls == 1
    assert valkey.writes[-1][2] == REPORT_CACHE_TTL_SECONDS == 600


@pytest.mark.asyncio
async def test_generation_rotation_makes_the_next_read_recompute(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    running_contest: Contest,
) -> None:
    valkey = _FakeValkey()
    calls = 0

    async def compute(*_args: Any, **_kwargs: Any) -> ContestReportPageData:
        nonlocal calls
        calls += 1
        return _page_data(str(calls))

    monkeypatch.setattr(contest_report_cache, "_compute_page_data", compute)
    first = await get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)

    await invalidate_contest_report_cache(valkey, str(running_contest.id))
    second = await get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)

    assert first.report["marker"] == "1"
    assert second.report["marker"] == "2"
    assert calls == 2


@pytest.mark.asyncio
async def test_invalidation_during_build_cannot_publish_stale_data_as_current(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    running_contest: Contest,
) -> None:
    valkey = _FakeValkey()
    build_started = asyncio.Event()
    release_build = asyncio.Event()
    calls = 0

    async def compute(*_args: Any, **_kwargs: Any) -> ContestReportPageData:
        nonlocal calls
        calls += 1
        if calls == 1:
            build_started.set()
            await release_build.wait()
        return _page_data(str(calls))

    monkeypatch.setattr(contest_report_cache, "_compute_page_data", compute)
    stale_build = asyncio.create_task(
        get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)
    )
    await build_started.wait()
    await invalidate_contest_report_cache(valkey, str(running_contest.id))
    release_build.set()
    await stale_build

    current = await get_contest_report_page_data(
        session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey
    )

    assert current.report["marker"] == "2"
    assert calls == 2


@pytest.mark.asyncio
async def test_malformed_or_old_payload_is_treated_as_a_miss(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    running_contest: Contest,
) -> None:
    valkey = _FakeValkey()
    generation_key = contest_report_generation_key(str(running_contest.id))
    valkey.values[generation_key] = "generation"
    data_key = contest_report_data_key(str(running_contest.id), "generation", None)
    valkey.values[data_key] = '{"version":0,"report":{},"chart_data":{}}'

    async def compute(*_args: Any, **_kwargs: Any) -> ContestReportPageData:
        return _page_data("fresh")

    monkeypatch.setattr(contest_report_cache, "_compute_page_data", compute)

    result = await get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)

    assert result == _page_data("fresh")


@pytest.mark.asyncio
async def test_site_scopes_do_not_share_entries(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    running_contest: Contest,
) -> None:
    valkey = _FakeValkey()
    calls: list[str | None] = []

    async def compute(
        _session: AsyncSession,
        _contest: Contest,
        site_id: str | None,
        _enrolled_teams: int,
    ) -> ContestReportPageData:
        calls.append(site_id)
        return _page_data(site_id or "all")

    monkeypatch.setattr(contest_report_cache, "_compute_page_data", compute)

    all_sites = await get_contest_report_page_data(
        session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey
    )
    one_site = await get_contest_report_page_data(
        session, running_contest, site_id="site-1", enrolled_teams=1, valkey=valkey
    )

    assert all_sites.report["marker"] == "all"
    assert one_site.report["marker"] == "site-1"
    assert calls == [None, "site-1"]


@pytest.mark.asyncio
async def test_valkey_failure_fails_open_without_using_local_cache(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    running_contest: Contest,
) -> None:
    class BrokenValkey:
        """Valkey stand-in that fails every read."""

        async def get(self, _key: str) -> None:
            raise ConnectionError("Valkey is down")

    calls = 0

    async def compute(*_args: Any, **_kwargs: Any) -> ContestReportPageData:
        nonlocal calls
        calls += 1
        return _page_data(str(calls))

    monkeypatch.setattr(contest_report_cache, "_compute_page_data", compute)
    valkey = BrokenValkey()

    await get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)
    await get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)

    assert calls == 2


@pytest.mark.asyncio
async def test_concurrent_first_reads_share_one_computation(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    running_contest: Contest,
) -> None:
    valkey = _FakeValkey()
    calls = 0
    release = asyncio.Event()

    async def compute(*_args: Any, **_kwargs: Any) -> ContestReportPageData:
        nonlocal calls
        calls += 1
        await release.wait()
        return _page_data("shared")

    monkeypatch.setattr(contest_report_cache, "_compute_page_data", compute)
    tasks = [
        asyncio.create_task(
            get_contest_report_page_data(session, running_contest, site_id=None, enrolled_teams=2, valkey=valkey)
        )
        for _ in range(3)
    ]
    await asyncio.sleep(0)
    release.set()

    results = await asyncio.gather(*tasks)

    assert calls == 1
    assert results == [_page_data("shared")] * 3


def test_payload_version_is_two() -> None:
    """Cache schema version must be 2 to roll over long-contest bucket payloads."""
    assert PAYLOAD_VERSION == 2


def test_chart_data_includes_time_window_minutes() -> None:
    """_chart_data must pass time_window_minutes to client payload."""
    report = SimpleNamespace(
        accept_pe=False,
        runs_distribution=[],
        accepted_distribution=[],
        time_windows=[],
        time_window_minutes=20,
        problem_race=[],
        performance=SimpleNamespace(solved_summary=None, solved_histogram=[]),
    )
    chart = contest_report_cache._chart_data(report)
    assert chart["time_window_minutes"] == 20
