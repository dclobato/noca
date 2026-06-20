#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin service for test-case CRUD and ZIP bulk-replace.

Test-case content lives on the shared filesystem under
``<root>/arena/<problem_id>/NNN.in|out``; the database row keeps only metadata
and the normalized (LF) on-disk byte sizes. Ordinals are maintained as a 1-based
contiguous sequence; deletes and moves renumber both rows and files in lockstep.

Inline (textarea) create/edit is gated: a normalized side larger than
``MAX_INLINE_TESTCASE_BYTES`` is rejected with ``ValueError`` and must be edited
offline through the single-case ZIP download/replace round-trip (no size cap).

All mutating functions return a ``Callable[[], None]`` (the second element of
their return tuple, or the sole return value for deletes) that the caller must
invoke **after** the DB transaction is committed.  This ensures the DB is the
authoritative source of truth: filesystem mutations only happen once the DB state
is durable.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem, ArenaTestCase
from shared.services.testcase_files import (
    delete_all_testcase_files,
    delete_testcase_files,
    read_testcase_preview,
    renumber_testcase_files,
    reorder_testcase_files,
    save_testcase_files,
)
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES, normalize_testcase_bytes, parse_testcases_zip


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TestCaseView:
    """Lightweight per-case view for list rendering (no full content load)."""

    id: str
    ordinal: int
    is_sample: bool
    has_explanation: bool
    input_preview: str
    output_preview: str
    input_size_bytes: int
    output_size_bytes: int
    is_large: bool


def _check_inline_size(in_bytes: bytes, out_bytes: bytes) -> None:
    """Reject inline content whose normalized side exceeds the gate threshold.

    Raises:
        ValueError: If either normalized side is larger than the limit.
    """
    if len(normalize_testcase_bytes(in_bytes)) > MAX_INLINE_TESTCASE_BYTES:
        raise ValueError(
            f"Input exceeds the {MAX_INLINE_TESTCASE_BYTES // 1024} KB inline-edit limit; "
            "edit this case offline via download/replace."
        )
    if len(normalize_testcase_bytes(out_bytes)) > MAX_INLINE_TESTCASE_BYTES:
        raise ValueError(
            f"Output exceeds the {MAX_INLINE_TESTCASE_BYTES // 1024} KB inline-edit limit; "
            "edit this case offline via download/replace."
        )


async def list_testcases(session: AsyncSession, problem_id: str) -> list[ArenaTestCase]:
    """Return all test cases for a problem ordered by ordinal."""
    result = await session.execute(
        select(ArenaTestCase).where(ArenaTestCase.problem_id == problem_id).order_by(ArenaTestCase.ordinal)
    )
    return list(result.scalars())


async def list_testcase_views(
    session: AsyncSession,
    problem_id: str,
    testcase_dir: Path,
) -> list[TestCaseView]:
    """Return lightweight per-case views with previews and size metadata.

    Previews are read from disk (first bytes only); sizes come from the DB
    columns, coalescing nulls (pre-backfill rows) to 0.
    """
    test_cases = await list_testcases(session, problem_id)
    views: list[TestCaseView] = []
    for tc in test_cases:
        in_preview, out_preview = read_testcase_preview(problem_id, tc.ordinal, testcase_dir)
        in_size = tc.input_size_bytes or 0
        out_size = tc.output_size_bytes or 0
        views.append(
            TestCaseView(
                id=tc.id,
                ordinal=tc.ordinal,
                is_sample=tc.is_sample,
                has_explanation=bool(tc.explanation),
                input_preview=in_preview,
                output_preview=out_preview,
                input_size_bytes=in_size,
                output_size_bytes=out_size,
                is_large=in_size > MAX_INLINE_TESTCASE_BYTES or out_size > MAX_INLINE_TESTCASE_BYTES,
            )
        )
    return views


async def get_testcase(
    session: AsyncSession,
    tc_id: str,
    *,
    problem_id: str,
) -> ArenaTestCase | None:
    """Fetch a single test case by id, scoped to a specific problem."""
    result = await session.execute(
        select(ArenaTestCase).where(
            ArenaTestCase.id == tc_id,
            ArenaTestCase.problem_id == problem_id,
        )
    )
    return result.scalar_one_or_none()


async def _next_ordinal(session: AsyncSession, problem_id: str) -> int:
    """Return the next available ordinal for a problem's test cases."""
    result = await session.execute(
        select(func.max(ArenaTestCase.ordinal)).where(ArenaTestCase.problem_id == problem_id)
    )
    current_max: int | None = result.scalar_one_or_none()
    return (current_max or 0) + 1


async def _count_testcases(session: AsyncSession, problem_id: str) -> int:
    """Return the current number of test cases for a problem."""
    result = await session.execute(
        select(func.count()).select_from(ArenaTestCase).where(ArenaTestCase.problem_id == problem_id)
    )
    return int(result.scalar_one())


async def create_testcase(
    session: AsyncSession,
    problem: ArenaProblem,
    *,
    input_content: str,
    output_content: str,
    is_sample: bool,
    explanation: str | None = None,
    testcase_dir: Path,
) -> tuple[ArenaTestCase, Callable[[], None]]:
    """Append a new test case to a problem (inline path, size-gated).

    Returns the new ``ArenaTestCase`` row (not yet committed) and a zero-arg
    callable that the caller must invoke **after** committing the transaction to
    write the pair of files to disk.

    Raises:
        ValueError: If either normalized side exceeds ``MAX_INLINE_TESTCASE_BYTES``.
    """
    in_bytes = input_content.encode("utf-8")
    out_bytes = output_content.encode("utf-8")
    _check_inline_size(in_bytes, out_bytes)

    in_norm = normalize_testcase_bytes(in_bytes)
    out_norm = normalize_testcase_bytes(out_bytes)
    now = _now()
    ordinal = await _next_ordinal(session, problem.id)
    tc = ArenaTestCase(
        id=str(uuid.uuid4()),
        problem_id=problem.id,
        ordinal=ordinal,
        is_sample=is_sample,
        input_size_bytes=len(in_norm),
        output_size_bytes=len(out_norm),
        explanation=explanation,
        created_at=now,
        updated_at=now,
    )
    session.add(tc)

    problem_id = problem.id

    def _write_files() -> None:
        save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir)

    return tc, _write_files


async def update_testcase(
    session: AsyncSession,
    tc: ArenaTestCase,
    *,
    input_content: str,
    output_content: str,
    is_sample: bool,
    explanation: str | None = None,
    testcase_dir: Path,
) -> tuple[ArenaTestCase, Callable[[], None]]:
    """Update mutable fields on an existing test case (inline path, size-gated).

    Returns the mutated ``ArenaTestCase`` (not yet committed) and a zero-arg
    callable that the caller must invoke **after** committing to write the
    updated files to disk.

    Raises:
        ValueError: If either normalized side exceeds ``MAX_INLINE_TESTCASE_BYTES``.
    """
    in_bytes = input_content.encode("utf-8")
    out_bytes = output_content.encode("utf-8")
    _check_inline_size(in_bytes, out_bytes)

    in_norm = normalize_testcase_bytes(in_bytes)
    out_norm = normalize_testcase_bytes(out_bytes)
    tc.input_size_bytes = len(in_norm)
    tc.output_size_bytes = len(out_norm)
    tc.is_sample = is_sample
    tc.explanation = explanation
    tc.updated_at = _now()

    problem_id = tc.problem_id
    ordinal = tc.ordinal

    def _write_files() -> None:
        save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir)

    return tc, _write_files


async def replace_single_testcase(
    session: AsyncSession,
    tc: ArenaTestCase,
    *,
    input_bytes: bytes,
    output_bytes: bytes,
    explanation: str | None,
    testcase_dir: Path,
) -> tuple[ArenaTestCase, Callable[[], None]]:
    """Replace a single case's content from an offline upload (no size cap).

    Returns the mutated ``ArenaTestCase`` (not yet committed) and a zero-arg
    callable that the caller must invoke **after** committing to write the
    replacement files to disk.
    """
    in_norm = normalize_testcase_bytes(input_bytes)
    out_norm = normalize_testcase_bytes(output_bytes)
    tc.input_size_bytes = len(in_norm)
    tc.output_size_bytes = len(out_norm)
    tc.explanation = explanation
    tc.updated_at = _now()

    problem_id = tc.problem_id
    ordinal = tc.ordinal

    def _write_files() -> None:
        save_testcase_files(problem_id, ordinal, input_bytes, output_bytes, testcase_dir)

    return tc, _write_files


async def toggle_sample(session: AsyncSession, tc: ArenaTestCase) -> ArenaTestCase:
    """Flip a test case's sample/secret flag without touching its content."""
    tc.is_sample = not tc.is_sample
    tc.updated_at = _now()
    return tc


async def delete_testcase(
    session: AsyncSession,
    tc: ArenaTestCase,
    *,
    testcase_dir: Path,
) -> Callable[[], None]:
    """Delete a test case and close the ordinal gap left by its removal.

    Performs only DB mutations (delete row + renumber ordinals of survivors).
    Returns a zero-arg callable that the caller must invoke **after** committing
    the transaction to remove the deleted file and renumber the remaining ones.
    """
    problem_id = tc.problem_id
    deleted_ordinal = tc.ordinal
    total = await _count_testcases(session, problem_id)

    await session.delete(tc)
    await session.flush()
    await session.execute(
        update(ArenaTestCase)
        .where(ArenaTestCase.problem_id == problem_id, ArenaTestCase.ordinal > deleted_ordinal)
        .values(ordinal=ArenaTestCase.ordinal - 1, updated_at=_now())
    )

    def _cleanup_files() -> None:
        delete_testcase_files(problem_id, deleted_ordinal, testcase_dir)
        for old_ordinal in range(deleted_ordinal + 1, total + 1):
            renumber_testcase_files(problem_id, old_ordinal, old_ordinal - 1, testcase_dir)

    return _cleanup_files


async def move_testcase(
    session: AsyncSession,
    tc: ArenaTestCase,
    new_ordinal: int,
    *,
    testcase_dir: Path,
) -> None:
    """Move a test case to a new 1-based ordinal inside its problem."""
    result = await session.execute(
        select(ArenaTestCase).where(ArenaTestCase.problem_id == tc.problem_id).order_by(ArenaTestCase.ordinal)
    )
    tcs = list(result.scalars())
    current_index = next((index for index, item in enumerate(tcs) if item.id == tc.id), None)
    if current_index is None:
        return

    moving = tcs.pop(current_index)
    destination_index = max(0, min(new_ordinal - 1, len(tcs)))
    tcs.insert(destination_index, moving)

    # Map each case's current ordinal to its destination ordinal for the files.
    ordinal_map = {item.ordinal: index for index, item in enumerate(tcs, start=1)}
    reorder_testcase_files(tc.problem_id, ordinal_map, testcase_dir)

    await _apply_testcase_order(session, tcs)


async def _apply_testcase_order(session: AsyncSession, tcs: list[ArenaTestCase]) -> None:
    """Persist a dense test-case order without violating ordinal uniqueness."""
    if not tcs:
        return
    now = _now()
    offset = len(tcs)
    problem_id = tcs[0].problem_id
    await session.execute(
        update(ArenaTestCase)
        .where(ArenaTestCase.problem_id == problem_id)
        .values(ordinal=ArenaTestCase.ordinal + offset, updated_at=now)
    )
    await session.flush()
    for new_ordinal, item in enumerate(tcs, start=1):
        await session.execute(
            update(ArenaTestCase).where(ArenaTestCase.id == item.id).values(ordinal=new_ordinal, updated_at=now)
        )
    await session.flush()


async def replace_all_from_zip(
    session: AsyncSession,
    problem: ArenaProblem,
    zip_bytes: bytes,
    *,
    default_is_sample: bool = False,
    testcase_dir: Path,
) -> tuple[int, Callable[[], None]]:
    """Replace all test cases for a problem from a ZIP archive (no size cap).

    Deletes all existing DB rows and inserts new metadata rows with sizes
    computed from in-memory normalization.  Returns the count of imported cases
    and a zero-arg callable that the caller must invoke **after** committing the
    transaction to delete the old files and write the new ones.

    Raises:
        ValueError: Propagated from ``parse_testcases_zip`` on malformed ZIP.
    """
    parsed = parse_testcases_zip(zip_bytes)

    await session.execute(delete(ArenaTestCase).where(ArenaTestCase.problem_id == problem.id))
    await session.flush()

    problem_id = problem.id
    pairs_for_disk: list[tuple[int, bytes, bytes]] = []
    now = _now()
    for ordinal, (in_bytes, out_bytes) in sorted(parsed.pairs.items()):
        in_norm = normalize_testcase_bytes(in_bytes)
        out_norm = normalize_testcase_bytes(out_bytes)
        tc = ArenaTestCase(
            id=str(uuid.uuid4()),
            problem_id=problem_id,
            ordinal=ordinal,
            is_sample=default_is_sample,
            input_size_bytes=len(in_norm),
            output_size_bytes=len(out_norm),
            explanation=parsed.explanations.get(ordinal),
            created_at=now,
            updated_at=now,
        )
        session.add(tc)
        pairs_for_disk.append((ordinal, in_bytes, out_bytes))

    def _apply_files() -> None:
        delete_all_testcase_files(problem_id, testcase_dir)
        for ordinal, in_b, out_b in pairs_for_disk:
            save_testcase_files(problem_id, ordinal, in_b, out_b, testcase_dir)

    return len(parsed.pairs), _apply_files
