#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_problem.py

Mixin for problem resource limits and test case metadata queries.
"""

from __future__ import annotations

from sqlalchemy import and_, case, func, literal, select

from autojudge.db._base import _DatabaseBase
from autojudge.types import ProblemLimits
from shared.db_schema import problem_language_limits as _problem_language_limit
from shared.db_schema import problems as _problem
from shared.db_schema import test_cases as _test_case


class _ProblemMixin(_DatabaseBase):
    """Database operations for problem limits and test case metadata."""

    async def get_problem_limits(self, problem_id: str, language_id: str) -> ProblemLimits:
        """
        Fetch resource limits for a problem with optional per-language overrides.

        Uses COALESCE so a language-specific entry overrides the base problem
        limits; if no override exists, the base problem values are returned.

        Args:
            problem_id: UUID of the problem.
            language_id: Language identifier used to look up per-language limits.

        Returns:
            ProblemLimits with the effective resource caps.

        Raises:
            LookupError: If problem_id does not exist.
        """
        row = await self._conn.execute(
            select(
                func.coalesce(_problem_language_limit.c.time_limit_ms, _problem.c.time_limit_ms).label("time_limit_ms"),
                func.coalesce(_problem_language_limit.c.memory_limit_kb, _problem.c.memory_limit_kb).label(
                    "memory_limit_kb"
                ),
                func.coalesce(_problem_language_limit.c.pids_limit, _problem.c.pids_limit).label("pids_limit"),
                func.coalesce(_problem_language_limit.c.output_limit_in_bytes, _problem.c.output_limit_in_bytes).label(
                    "output_limit_in_bytes"
                ),
                case(
                    (_problem_language_limit.c.problem_id.isnot(None), _problem_language_limit.c.repetitions),
                    else_=literal(1),
                ).label("repetitions"),
            )
            .select_from(
                _problem.outerjoin(
                    _problem_language_limit,
                    and_(
                        _problem_language_limit.c.problem_id == _problem.c.id,
                        _problem_language_limit.c.language_id == language_id,
                    ),
                )
            )
            .where(_problem.c.id == problem_id)
        )
        result = row.fetchone()
        if result is None:
            raise LookupError(f"Problem '{problem_id}' not found in database")
        return ProblemLimits(
            time_limit_ms=result.time_limit_ms,
            memory_limit_kb=result.memory_limit_kb,
            pids_limit=result.pids_limit,
            output_limit_in_bytes=result.output_limit_in_bytes,
            repetitions=result.repetitions,
        )

    async def get_test_case_id_map(self, problem_id: str) -> dict[int, str]:
        """
        Return ordinal → test_case_id mapping for a problem.

        The worker loads test-case data from the filesystem (by ordinal) but
        needs the UUID primary key to insert SubmissionTestResult rows.

        Args:
            problem_id: UUID of the problem.

        Returns:
            Dict mapping ordinal (int) to test_case UUID string.
        """
        rows = await self._conn.execute(
            select(_test_case.c.ordinal, _test_case.c.id)
            .where(_test_case.c.problem_id == problem_id)
            .order_by(_test_case.c.ordinal)
        )
        return {row.ordinal: row.id for row in rows.fetchall()}
