#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Resolving import journals left behind by a crash.

Both problem tables live in the shared schema, so one query answers "did that
import's transaction commit?" for either domain and no per-domain reconciler is
needed. This runs at application startup, alongside the other reaper
registrations, **and** before each import — a crashed import must not wait for
the next one to be cleaned up.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import arena_problems as _arena_problems
from shared.db_schema import problems as _problems
from shared.services.problem_package.journal import journal_root_for, reconcile_journals
from shared.services.problem_package.promotion import ImportDomain

logger = logging.getLogger(__name__)


async def reconcile_import_journals(
    session: AsyncSession,
    *,
    domain: ImportDomain,
    testcase_dir: Path,
    statement_dir: Path | None = None,
) -> int:
    """Resolve every stale import journal for one domain.

    Args:
        session: Session used only to check whether a problem row exists.
        domain: Which problem table the journals refer to.
        testcase_dir: The domain's configured test-case root.
        statement_dir: The domain's statement root, when it stores statements on
            disk. Passed so a journal naming it is recognized as in-bounds.

    Returns:
        The number of journals resolved.
    """
    roots = {testcase_dir.resolve()}
    if statement_dir is not None:
        roots.add(statement_dir.resolve())

    async def problem_exists(journal_domain: str, problem_id: str) -> bool:
        table = _arena_problems if journal_domain == "arena" else _problems
        found = await session.scalar(select(table.c.id).where(table.c.id == problem_id))
        return found is not None

    return await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset(roots),
    )
