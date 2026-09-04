#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Precomputed per-user Arena statistics.

The rating worker calls :func:`compute_all_user_statistics` periodically to
aggregate each user's judged submissions into a single JSON snapshot
(``arena_user_statistics``), which the Arena public profile page reads directly
so no heavy aggregate query runs during a request. Per-**problem** snapshots are
built by :mod:`shared.services.arena_problem_stats`.

Each submission contributes its most-recent non-``SUPERSEDED`` judgment (the
"active" judgment), mirroring the selection used by the Arena submission list.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import languages as _languages
from shared.db_schema.arena import arena_submission_judgments as _judgments
from shared.db_schema.arena import arena_submissions as _submissions
from shared.db_schema.arena import arena_user_statistics as _arena_user_statistics
from shared.services.arena_query_helpers import active_arena_judgment_subquery

__all__ = ["compute_all_user_statistics"]


def _build_user_payload(rows: list[tuple[str, str]], lang_names: dict[str, str]) -> dict[str, Any]:
    """Assemble the per-user statistics payload from judged submission rows.

    Args:
        rows: Rows of ``(language_id, final_verdict)`` for the user's judged
            submissions.
        lang_names: Mapping of language id to display name.

    Returns:
        dict: JSON-serializable statistics payload.
    """
    verdict_counts: dict[str, int] = defaultdict(int)
    language_counts: dict[str, int] = defaultdict(int)

    for language_id, verdict in rows:
        verdict_counts[verdict] += 1
        language_counts[language_id] += 1

    def _name(language_id: str) -> str:
        return lang_names.get(language_id, language_id)

    verdicts = [
        {"verdict": verdict, "count": count}
        for verdict, count in sorted(verdict_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    languages = [
        {"language_id": lid, "name": _name(lid), "count": count}
        for lid, count in sorted(language_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "total_submissions": len(rows),
        "verdicts": verdicts,
        "languages": languages,
    }


async def compute_all_user_statistics(session: AsyncSession) -> int:
    """Recompute and persist per-user statistics snapshots.

    Replaces every row in ``arena_user_statistics`` with a freshly computed
    snapshot for each user that has at least one judged submission. All
    submissions are counted (no rating exclusion): these are the user's own
    submissions. Does **not** commit; the caller owns the transaction.

    Args:
        session: Active async database session.

    Returns:
        int: Number of users with a statistics snapshot written.
    """
    lang_names = {row.id: row.name for row in (await session.execute(select(_languages.c.id, _languages.c.name))).all()}

    active = active_arena_judgment_subquery()
    stmt = (
        select(
            _submissions.c.user_id,
            _submissions.c.language_id,
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
        .where(_judgments.c.final_verdict.isnot(None))
    )

    by_user: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in (await session.execute(stmt)).all():
        by_user[row.user_id].append((row.language_id, row.final_verdict))

    # Full rebuild: delete all rows then re-insert. Between the delete and the
    # caller's commit, concurrent reads return None (empty payload). This is the
    # same intentional trade-off as compute_all_problem_statistics; the read side
    # renders an "empty" state gracefully. Switch to upsert if the gap becomes a
    # concern at higher traffic.
    await session.execute(delete(_arena_user_statistics))
    for user_id, rows in by_user.items():
        payload = _build_user_payload(rows, lang_names)
        await session.execute(_arena_user_statistics.insert().values(user_id=user_id, data=payload))

    return len(by_user)
