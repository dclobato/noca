#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_base.py

Base class for worker DB access: connection holder and shared private helpers
used by all mixin classes (_get_judgment_state, _insert_judgment_audit,
_insert_result_row_once).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, Table, and_, exists, literal, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from autojudge.types import JudgmentOwnershipLost, _JudgmentState
from shared.db_schema import submission_judgment_audit as _submission_judgment_audit
from shared.db_schema import submission_judgments as _submission_judgment
from shared.enumerations import JudgmentStatus, ProfilingStatus, Verdict
from shared.timing import compute_timestamp_seconds

JUDGMENT_DISPATCHABLE_STATUSES = (
    JudgmentStatus.QUEUED,
    JudgmentStatus.DISPATCHED,
    JudgmentStatus.JUDGING,
)
PROFILING_DISPATCHABLE_STATUSES = (
    ProfilingStatus.QUEUED,
    ProfilingStatus.DISPATCHED,
    ProfilingStatus.RUNNING,
)

#: Test-result columns re-measured on every execution. A duplicate dispatch
#: re-runs the program, so these legitimately differ between two attempts at the
#: same job and never by themselves indicate an inconsistency.
RESULT_VOLATILE_COLUMNS = (
    "wall_time_ms",
    "memory_kb",
    "exit_code",
    "exit_signal",
    "stdout_excerpt",
    "stderr_excerpt",
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class AttemptClaim:
    """One attempt's claim on a queued run, as stamped at dispatch.

    Covers every run kind the worker owns end to end: contest and Arena
    judgments, profiling runs, and solution-test runs.

    The claim is attempt-scoped rather than worker-scoped: two attempts at the
    same run routinely share a ``worker_id`` (the reaper's requeue is usually
    picked up by the same host, and often by the same process), so only a
    per-attempt token can tell them apart.

    Attributes:
        table: Run table holding the claim.
        row_id: UUID of the run being claimed.
        attempt_token: Token stamped by this attempt's dispatch.
    """

    table: Table
    row_id: str
    attempt_token: str


class _DatabaseBase:
    """Holds the async connection and shared private helpers for all DB mixins."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    @property
    def connection(self) -> AsyncConnection:
        """Expose the underlying async connection for worker-side helpers."""
        return self._conn

    async def _get_judgment_state(self, judgment_id: str) -> _JudgmentState:
        """
        Fetch a lightweight snapshot of a judgment's current state.

        Args:
            judgment_id: UUID string of the judgment.

        Returns:
            _JudgmentState with submission_id, status, and autojudge_verdict.

        Raises:
            LookupError: If the judgment does not exist.
        """
        row = await self._conn.execute(_submission_judgment.select().where(_submission_judgment.c.id == judgment_id))
        result = row.fetchone()
        if result is None:
            raise LookupError(f"Judgment '{judgment_id}' not found in database")
        return {
            "submission_id": cast(str, result.submission_id),
            "status": cast(JudgmentStatus | None, result.status),
            "autojudge_verdict": cast(Verdict | None, result.autojudge_verdict),
        }

    def _claim_predicate(self, claim: AttemptClaim) -> ColumnElement[bool]:
        """Return a WHERE fragment matching a run this attempt still owns."""
        return and_(
            claim.table.c.id == claim.row_id,
            claim.table.c.attempt_token == claim.attempt_token,
        )

    async def _holds_claim(self, claim: AttemptClaim | None) -> bool:
        """Report whether this attempt still holds the run's claim.

        Args:
            claim: The attempt's claim, or None when the caller has no claim to
                assert (pre-dispatch failures and legacy rows).

        Returns:
            True when the claim is absent (nothing to check) or still stamped on
            the run row.
        """
        if claim is None:
            return True
        held = await self._conn.execute(select(claim.table.c.id).where(self._claim_predicate(claim)))
        return held.scalar_one_or_none() is not None

    def _claimed_insert_source(
        self,
        table: Table,
        values: Mapping[str, Any],
        claim: AttemptClaim | None,
    ) -> Select[Any]:
        """Build the SELECT feeding a claim-conditional INSERT.

        Folding the ownership test into the INSERT's source leaves no window
        between checking the claim and writing the row.

        Args:
            table: Table receiving the row, used for per-column bind types.
            values: Full column mapping for the insert.
            claim: This attempt's claim, or None to write unconditionally.

        Returns:
            A SELECT of literal values, filtered by the claim when there is one.
        """
        source = select(*(literal(values[name], table.c[name].type).label(name) for name in values))
        if claim is None:
            return source
        return source.where(exists(select(claim.table.c.id).where(self._claim_predicate(claim))))

    async def _insert_claimed_row(
        self,
        table: Table,
        *,
        values: Mapping[str, Any],
        claim: AttemptClaim | None,
    ) -> None:
        """Insert one row, but only while this attempt owns the run.

        For result tables with no unique key of their own, where a stale
        attempt's late write would not collide with anything — it would simply
        interleave its rows with the new owner's. Does not commit.

        Args:
            table: Table receiving the row.
            values: Full column mapping for the insert.
            claim: This attempt's claim, or None to write unconditionally.

        Raises:
            JudgmentOwnershipLost: If the claim no longer holds.
        """
        inserted_id = (
            await self._conn.execute(
                table.insert()
                .from_select(list(values), self._claimed_insert_source(table, values, claim))
                .returning(table.c.id)
            )
        ).scalar_one_or_none()
        if inserted_id is None:
            raise JudgmentOwnershipLost(f"Discarded {table.name} row: the run was claimed by another attempt")

    async def _insert_result_row_once(
        self,
        table: Table,
        *,
        values: Mapping[str, Any],
        index_elements: Sequence[str],
        identity_columns: Sequence[str],
        volatile_columns: Sequence[str],
        claim: AttemptClaim | None,
    ) -> None:
        """Insert one judging result row for the attempt that owns the judgment.

        A reaper requeue can hand a judgment to a replacement attempt while the
        original one is still running, which makes two kinds of collision
        possible. The first is a genuine key collision, when both attempts reach
        the insert; the second — and the dangerous one — is the older attempt
        writing *after* the replacement's dispatch wiped the result rows, where
        there is nothing to collide with and the stale row simply lands in the
        new attempt's result set.

        Both are handled by making the insert conditional on the claim, in one
        statement so no window exists between checking ownership and writing:

        - the row is inserted: this attempt owns the judgment, nothing else to do;
        - nothing inserted and the claim is gone: the write belonged to work
          another attempt took over, so it is dropped and the attempt aborts;
        - nothing inserted while the claim holds: the same attempt is replaying
          its own write. The committed row stands, and drift in
          ``volatile_columns`` (re-measured time, memory, output) is logged;
        - the replay disagrees on ``identity_columns``: the two writes describe
          different failures, which no ordering of one attempt's own work can
          produce, so it is raised rather than reconciled.

        Does not commit — the caller commits.

        Args:
            table: Result table receiving the row.
            values: Full column mapping for the insert.
            index_elements: Column names of the unique constraint to conflict on.
            identity_columns: Columns that must agree for a replay to be the
                same work.
            volatile_columns: Columns re-measured on each execution, whose
                divergence is expected and only logged.
            claim: This attempt's claim on the judgment, or None to write
                unconditionally (used by paths with no claim to assert).

        Raises:
            JudgmentOwnershipLost: If the claim no longer holds.
            RuntimeError: If an existing row disagrees on ``identity_columns``.
        """
        insert = sqlite_insert if self._conn.dialect.name == "sqlite" else postgresql_insert
        inserted_id = (
            await self._conn.execute(
                insert(table)
                .from_select(list(values), self._claimed_insert_source(table, values, claim))
                .on_conflict_do_nothing(index_elements=[table.c[name] for name in index_elements])
                .returning(table.c.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            return

        if not await self._holds_claim(claim):
            raise JudgmentOwnershipLost(
                f"Discarded {table.name} row for "
                f"{', '.join(f'{name}={values[name]!r}' for name in index_elements)}: "
                "the judgment was claimed by another attempt"
            )

        compared = (*identity_columns, *volatile_columns)
        existing = (
            (
                await self._conn.execute(
                    select(*(table.c[name] for name in compared)).where(
                        *(table.c[name] == values[name] for name in index_elements)
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            # Only a dispatch deletes result rows, and a dispatch also replaces
            # the claim, so a vanished row means the claim check above raced a
            # takeover that has since completed.
            raise JudgmentOwnershipLost(
                f"Discarded {table.name} row for "
                f"{', '.join(f'{name}={values[name]!r}' for name in index_elements)}: "
                "its committed row was cleared by a concurrent dispatch"
            )

        conflicting = [name for name in identity_columns if existing[name] != values[name]]
        if conflicting:
            raise RuntimeError(
                f"Conflicting {table.name} replay for "
                f"{', '.join(f'{name}={values[name]!r}' for name in index_elements)} "
                f"(different columns: {', '.join(conflicting)})"
            )

        drifted = [name for name in volatile_columns if existing[name] != values[name]]
        logger.warning(
            "Accepted replayed %s row for %s; keeping the committed row (drifted columns: %s)",
            table.name,
            ", ".join(f"{name}={values[name]!r}" for name in index_elements),
            ", ".join(drifted) or "none",
        )

    async def _insert_judgment_audit(
        self,
        judgment_id: str,
        submission_id: str,
        event_type: str,
        from_status: JudgmentStatus | None,
        to_status: JudgmentStatus | None,
        from_verdict: Verdict | None,
        to_verdict: Verdict | None,
        message: str | None = None,
        contest_start_time: datetime | None = None,
    ) -> None:
        """
        Insert one judgment audit row (does not commit — caller commits).

        Args:
            judgment_id: Judgment being audited.
            submission_id: Parent submission.
            event_type: Audit event category string.
            from_status: Previous judgment status.
            to_status: New judgment status.
            from_verdict: Previous autojudge verdict.
            to_verdict: New autojudge verdict.
            message: Optional human-readable note.
            contest_start_time: Used to compute timestamp_seconds; may be None.
        """
        now = _utcnow()
        await self._conn.execute(
            _submission_judgment_audit.insert().values(
                id=str(uuid.uuid4()),
                judgment_id=judgment_id,
                submission_id=submission_id,
                actor_user_id=None,
                event_source="WORKER",
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                from_verdict=from_verdict,
                to_verdict=to_verdict,
                message=message,
                created_at=now,
                timestamp_seconds=compute_timestamp_seconds(contest_start_time, now) if contest_start_time else None,
            )
        )
