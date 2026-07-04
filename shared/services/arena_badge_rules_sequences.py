#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Sequence and burst Arena badge rules."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_submission_judgments as _judgments
from shared.db_schema.arena import arena_submissions as _submissions
from shared.enumerations import ArenaBadge, JudgmentStatus, Verdict
from shared.services.arena_badge_data import AcEvent, NonAcEvent, as_utc, award_badge
from shared.services.arena_query_helpers import active_arena_judgment_subquery

_THIS_IS_THE_WAY_RUN = 15
_LOCOCODER_COUNT = 3
_LOCOCODER_WINDOW_SECONDS = 90


async def award_this_is_the_way(session: AsyncSession, events: list[AcEvent]) -> int:
    """Award THIS_IS_THE_WAY for a 15-AC run over distinct problems."""
    user_ids = {e.user_id for e in events}
    if not user_ids:
        return 0

    active = active_arena_judgment_subquery()
    rows = (
        await session.execute(
            select(
                _submissions.c.user_id,
                _submissions.c.problem_id,
                _submissions.c.id,
                _submissions.c.created_at,
                _judgments.c.final_verdict,
            )
            .select_from(
                _submissions.join(active, active.c.submission_id == _submissions.c.id).join(
                    _judgments,
                    and_(
                        _judgments.c.submission_id == _submissions.c.id,
                        _judgments.c.created_at == active.c.max_created_at,
                    ),
                )
            )
            .where(
                _submissions.c.user_id.in_(user_ids),
                _judgments.c.status == JudgmentStatus.DONE.value,
                _judgments.c.final_verdict.isnot(None),
            )
            .order_by(_submissions.c.user_id, _submissions.c.created_at, _submissions.c.id)
        )
    ).all()

    awarded = 0
    run_problems: dict[str, set[str]] = defaultdict(set)
    run_lengths: dict[str, int] = defaultdict(int)
    qualified: set[str] = set()
    for row in rows:
        if row.user_id in qualified:
            continue
        if row.final_verdict == Verdict.AC.value and row.problem_id not in run_problems[row.user_id]:
            run_problems[row.user_id].add(row.problem_id)
            run_lengths[row.user_id] += 1
        elif row.final_verdict == Verdict.AC.value:
            run_problems[row.user_id] = {row.problem_id}
            run_lengths[row.user_id] = 1
        else:
            run_problems[row.user_id].clear()
            run_lengths[row.user_id] = 0

        if run_lengths[row.user_id] >= _THIS_IS_THE_WAY_RUN:
            qualified.add(row.user_id)

    for user_id in qualified:
        if await award_badge(session, user_id, ArenaBadge.THIS_IS_THE_WAY):
            awarded += 1
    return awarded


async def award_lococoder(session: AsyncSession, events: list[NonAcEvent]) -> int:
    """Award LOCO_CODER for 3 non-AC verdicts on one problem within 90 seconds."""
    grouped: dict[tuple[str, str], list[NonAcEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.user_id, event.problem_id)].append(event)

    awarded = 0
    window = timedelta(seconds=_LOCOCODER_WINDOW_SECONDS)
    for (user_id, _), rows in grouped.items():
        recent: deque[NonAcEvent] = deque()
        for row in sorted(rows, key=lambda e: (as_utc(e.created_at), e.submission_id)):
            recent.append(row)
            while as_utc(row.created_at) - as_utc(recent[0].created_at) > window:
                recent.popleft()
            if len(recent) >= _LOCOCODER_COUNT:
                if await award_badge(session, user_id, ArenaBadge.LOCO_CODER):
                    awarded += 1
                break
    return awarded
