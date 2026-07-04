#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for precomputed per-user Arena statistics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from _helpers import _make_language, _make_problem, _make_submission, _make_user
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.user_stats_service import get_user_statistics
from shared.db_schema.arena import arena_submission_judgments, arena_user_statistics
from shared.enumerations import JudgmentStatus, Verdict
from shared.services.arena_stats import compute_all_user_statistics


async def _add_judgment(
    session: AsyncSession,
    *,
    submission_id: str,
    verdict: Verdict | None,
    status: JudgmentStatus = JudgmentStatus.DONE,
    created_at: datetime | None = None,
) -> None:
    """Insert a judgment for a statistics test submission."""
    await session.execute(
        insert(arena_submission_judgments).values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=status.value,
            final_verdict=verdict.value if verdict else None,
            autojudge_verdict=verdict.value if verdict else None,
            created_at=created_at or datetime.now(UTC),
        )
    )


@pytest.mark.asyncio
async def test_compute_user_statistics_groups_each_users_active_judgments(
    session: AsyncSession,
) -> None:
    """Snapshots contain per-user verdict and language distributions."""
    first_user = await _make_user(session)
    second_user = await _make_user(session)
    problem = await _make_problem(session, first_user)
    language = await _make_language(session)

    first_ac = await _make_submission(session, first_user.id, problem.id, language.id)
    first_wa = await _make_submission(session, first_user.id, problem.id, language.id)
    second_ce = await _make_submission(session, second_user.id, problem.id, language.id)
    unjudged = await _make_submission(session, second_user.id, problem.id, language.id)
    await _add_judgment(session, submission_id=first_ac.id, verdict=Verdict.AC)
    await _add_judgment(session, submission_id=first_wa.id, verdict=Verdict.WA)
    await _add_judgment(session, submission_id=second_ce.id, verdict=Verdict.CE)
    await _add_judgment(session, submission_id=unjudged.id, verdict=None, status=JudgmentStatus.JUDGING)

    count = await compute_all_user_statistics(session)
    await session.commit()

    assert count == 2
    rows = (await session.execute(select(arena_user_statistics.c.user_id, arena_user_statistics.c.data))).all()
    by_user = {row.user_id: row.data for row in rows}
    assert by_user[first_user.id]["total_submissions"] == 2
    assert {item["verdict"]: item["count"] for item in by_user[first_user.id]["verdicts"]} == {
        Verdict.AC.value: 1,
        Verdict.WA.value: 1,
    }
    assert by_user[first_user.id]["languages"] == [{"language_id": language.id, "name": language.name, "count": 2}]
    assert by_user[second_user.id]["total_submissions"] == 1
    assert by_user[second_user.id]["verdicts"] == [{"verdict": Verdict.CE.value, "count": 1}]


@pytest.mark.asyncio
async def test_compute_user_statistics_replaces_stale_snapshots(session: AsyncSession) -> None:
    """A rebuild removes snapshots for users without judged submissions."""
    user = await _make_user(session)
    await session.flush()
    await session.execute(
        arena_user_statistics.insert().values(
            user_id=user.id,
            data={"total_submissions": 99, "verdicts": [], "languages": []},
        )
    )

    count = await compute_all_user_statistics(session)
    await session.commit()

    assert count == 0
    assert (
        await session.scalar(select(arena_user_statistics.c.user_id).where(arena_user_statistics.c.user_id == user.id))
        is None
    )


@pytest.mark.asyncio
async def test_get_user_statistics_returns_payload_with_computed_timestamp(
    session: AsyncSession,
) -> None:
    """The Arena read service augments a stored snapshot with its timestamp."""
    user = await _make_user(session)
    computed_at = datetime(2026, 6, 22, 14, 30, tzinfo=UTC)
    await session.execute(
        arena_user_statistics.insert().values(
            user_id=user.id,
            data={"total_submissions": 1, "verdicts": [], "languages": []},
            computed_at=computed_at,
        )
    )
    await session.commit()

    payload = await get_user_statistics(session, user.id)

    assert payload is not None
    assert payload["total_submissions"] == 1
    assert payload["verdicts"] == []
    assert payload["languages"] == []
    assert payload["computed_at"].startswith("2026-06-22T14:30:00")
