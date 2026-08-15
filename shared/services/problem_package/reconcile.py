#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Resolving promotion journals left behind by a crash.

Both problem tables live in the shared schema, so one query answers "did that
transaction commit?" for either domain and no per-domain reconciler is needed.
This runs at application startup, alongside the other reaper registrations,
**and** before each import — a crashed attempt must not wait for the next import
to be cleaned up.

Two signals, because an import and an edit are not the same question: an import
is resolved by whether the problem row exists, while an edited problem exists
either way, so its journal is resolved by the row's ``artifact_generation``.
Editor Saves journal into the same directory, so they are reconciled by the same
startup and pre-import passes without any new call site.

That last part is why a third question has to be asked first: **is this journal
even stale?** The pre-import pass runs while the application is serving, so a
Save may be between its promotion and its commit right now -- and from outside
its transaction that is indistinguishable from a lost commit, because the row
still holds the previous generation. Resolving it there would restore the
quarantined originals under a live Save, which would then commit rows describing
files that had been moved back. A Save holds the problem row locked for exactly
that window (it bumps ``artifact_generation`` before journalling and commits
after promoting), so a row another transaction holds is the signal, and such a
journal is left for the next pass. Ownership is a *scope* rather than a question:
it is held until the journal has been resolved and cleared, because a Save that
started between the answer and the recovery would be trampled by it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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

    async def problem_generation(journal_domain: str, problem_id: str) -> int | None:
        table = _arena_problems if journal_domain == "arena" else _problems
        stored = await session.scalar(
            select(table.c.artifact_generation).where(table.c.id == problem_id),
        )
        return None if stored is None else int(stored)

    @asynccontextmanager
    async def edit_guard(journal_domain: str, problem_id: str) -> AsyncIterator[bool]:
        """Own one problem for the whole of its journal's resolution.

        ``SKIP LOCKED`` returns no row for two very different reasons -- the row is
        gone, or someone else holds it -- and only the second means "in flight", so
        the existence check decides between them. It is deliberately not a blocking
        ``FOR UPDATE``: this runs before every import, and waiting on another
        author's Save would stall an unrelated import for as long as that Save
        takes.

        The lock is held until the journal has been resolved *and* cleared, then
        dropped with the savepoint. Probing and releasing first would leave the
        window this exists to close -- a Save starting between the answer and the
        recovery. Dropping it afterwards is safe and necessary: an import holds its
        transaction open until it commits, and the lock would otherwise block every
        author whose problem this pass merely inspected.
        """
        table = _arena_problems if journal_domain == "arena" else _problems
        if not await problem_exists(journal_domain, problem_id):
            # No row to own, and no Save can be running against one that does not
            # exist. Recovery decides from the missing row itself.
            yield True
            return
        savepoint = await session.begin_nested()
        try:
            owned = await session.scalar(
                select(table.c.id).where(table.c.id == problem_id).with_for_update(skip_locked=True),
            )
            yield owned is not None
        finally:
            await savepoint.rollback()

    return await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset(roots),
        problem_generation=problem_generation,
        edit_guard=edit_guard,
    )
