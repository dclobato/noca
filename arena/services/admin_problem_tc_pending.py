#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Test-case edits deferred to the problem edit form's single Save.

The edit page does not remove or add test cases as you click: it marks removals
in a hidden ``tc_remove_ids`` field and collects new rows as ``tc_in_N`` /
``tc_out_N`` groups, all of which ride the one form. This module turns that raw
form data into service calls.

Filesystem work is deferred: the returned callables are meant to run only after
the caller commits, so a rolled-back save never deletes a live test-case file nor
leaves an orphaned one behind.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem
from arena.services import admin_problem_tc_service

PostCommitCallbacks = tuple[list[Callable[[], None]], list[Callable[[], None]]]


def _add_indices(form_data: Mapping[str, Any]) -> list[int]:
    """Return the sorted indices of the inline add-rows present in ``form_data``."""
    return sorted(
        {
            int(key.rsplit("_", 1)[1])
            for key in form_data
            if (key.startswith("tc_in_") or key.startswith("tc_out_")) and key.rsplit("_", 1)[1].isdigit()
        }
    )


def _row_is_filled(form_data: Mapping[str, Any], index: int) -> bool:
    """Report whether add-row ``index`` carries any input or output content."""
    return bool(str(form_data.get(f"tc_in_{index}", "")) or str(form_data.get(f"tc_out_{index}", "")))


def removal_ids(form_data: Mapping[str, Any]) -> set[str]:
    """Return the test-case ids the user marked for removal on the edit page."""
    raw = str(form_data.get("tc_remove_ids", "") or "")
    return {value.strip() for value in raw.split(",") if value.strip()}


async def apply_pending_testcases(
    session: AsyncSession,
    problem: ArenaProblem,
    form_data: Mapping[str, Any],
    *,
    testcase_dir: Path,
) -> PostCommitCallbacks:
    """Apply the removals and additions the edit form deferred to Save.

    Removals run before the additions so surviving ordinals stay contiguous, and
    in descending ordinal order so the renumbering churns as few files as
    possible.

    Args:
        session: Open Arena session; nothing is committed here.
        problem: The problem being saved.
        form_data: Raw submitted form.
        testcase_dir: Root of the Arena test-case storage.

    Returns:
        ``(file_cleanups, file_writes)`` — callables to run after the commit.

    Raises:
        ValueError: If an added row fails test-case validation, or if the save's
            net outcome would leave an interactive problem with no secret case.
    """
    to_remove_ids = removal_ids(form_data)
    add_indices = [index for index in _add_indices(form_data) if _row_is_filled(form_data, index)]

    cleanups: list[Callable[[], None]] = []
    existing = await admin_problem_tc_service.list_testcases(session, problem.id)
    to_remove = [tc for tc in existing if tc.id in to_remove_ids]

    # Judge the invariant on the save's net outcome: removing every existing case
    # while adding replacements in the same submit is legitimate.
    if len(existing) - len(to_remove) + len(add_indices) < 1 and await admin_problem_tc_service.has_custom_validator(
        session, problem.id
    ):
        raise ValueError("An interactive problem needs at least one secret test case.")

    if to_remove:
        for tc in sorted(to_remove, key=lambda item: item.ordinal, reverse=True):
            cleanups.append(await admin_problem_tc_service.delete_testcase(session, tc, testcase_dir=testcase_dir))

    file_writes: list[Callable[[], None]] = []
    for index in add_indices:
        explanation = str(form_data.get(f"tc_explanation_{index}", "")).strip()
        _tc, write_files = await admin_problem_tc_service.create_testcase(
            session,
            problem,
            input_content=str(form_data.get(f"tc_in_{index}", "")),
            output_content=str(form_data.get(f"tc_out_{index}", "")),
            is_sample=bool(form_data.get(f"tc_is_sample_{index}")),
            explanation=explanation or None,
            testcase_dir=testcase_dir,
        )
        file_writes.append(write_files)
        await session.flush()

    return cleanups, file_writes
