#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the promotion journal's format, versions, and refusal rules.

The swap's own failure points live in ``test_problem_package_edit_swap.py``; this
module covers what recovery does with a journal it should *not* act on, and the
version-1 compatibility that keeps an upgrade from stranding an in-flight import.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.services.problem_package.edit_swap import EditArtifactSwap
from shared.services.problem_package.journal import (
    JOURNAL_SUFFIX,
    ImportJournal,
    JournalEntry,
    JournalKind,
    PromotionState,
    _resolution_order,
    journal_root_for,
    read_journal,
    reconcile_journals,
    write_journal,
)

PROBLEM_ID = "problem-under-test"


def _setup(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    """Create a test-case root holding one problem's existing files."""
    testcase_dir = tmp_path / "testcases" / "contest"
    problem_dir = testcase_dir / PROBLEM_ID
    problem_dir.mkdir(parents=True)
    files = {"001.in": b"1 2\n", "001.out": b"3\n"}
    for name, content in files.items():
        (problem_dir / name).write_bytes(content)
    return testcase_dir, files


def _snapshot(directory: Path) -> dict[str, bytes]:
    """Return every file in ``directory`` as ``{name: bytes}``."""
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}


async def _reconcile(testcase_dir: Path, stored_generation: int | None) -> int:
    """Run reconciliation with a stored generation standing in for the database."""

    async def problem_exists(_domain: str, _problem_id: str) -> bool:  # pragma: no cover - edit path
        raise AssertionError("an edit journal must not be resolved on problem existence")

    async def problem_generation(_domain: str, _problem_id: str) -> int | None:
        return stored_generation

    return await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir}),
        problem_generation=problem_generation,
    )


@pytest.mark.asyncio
async def test_an_edit_journal_is_left_untouched_without_a_generation_lookup(tmp_path: Path) -> None:
    """The fence is the only valid signal for an edit; never guess with the other one."""
    testcase_dir, _ = _setup(tmp_path)
    swap = EditArtifactSwap(
        domain="contest",
        journal_root=journal_root_for(testcase_dir),
        testcase_dir=testcase_dir,
        problem_id=PROBLEM_ID,
        expected_generation=1,
    )
    swap.stage_test_cases()
    swap.write_journal()
    swap.promote()
    promoted = _snapshot(testcase_dir / PROBLEM_ID)

    async def problem_exists(_domain: str, _problem_id: str) -> bool:
        return True

    resolved = await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir}),
    )

    assert resolved == 0
    assert list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))
    # Nothing was touched, not merely "nothing was counted": a resolver that
    # deleted artifacts and returned 0 would otherwise pass this test.
    assert _snapshot(testcase_dir / PROBLEM_ID) == promoted
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] != []


@pytest.mark.asyncio
async def test_an_edit_journal_naming_paths_outside_the_roots_changes_nothing(tmp_path: Path) -> None:
    testcase_dir, original = _setup(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    victim = outside / "precious.txt"
    victim.write_text("do not delete me")
    quarantine = outside / "quarantined"
    quarantine.mkdir()
    (quarantine / "001.in").write_text("attacker content\n")

    write_journal(
        ImportJournal(
            path=journal_root_for(testcase_dir) / f"token{JOURNAL_SUFFIX}",
            problem_id=PROBLEM_ID,
            domain="contest",
            entries=(
                JournalEntry(
                    staged=outside / "staged",
                    target=testcase_dir / PROBLEM_ID,
                    root=outside,
                    quarantine=quarantine,
                    target_existed=True,
                ),
            ),
            state=PromotionState.PROMOTED,
            kind=JournalKind.EDIT,
            expected_generation=5,
        )
    )

    resolved = await _reconcile(testcase_dir, stored_generation=0)

    assert resolved == 1
    assert victim.exists()
    assert (quarantine / "001.in").exists()
    assert _snapshot(testcase_dir / PROBLEM_ID) == original


@pytest.mark.asyncio
async def test_an_edit_journal_without_a_generation_fence_is_left_in_place(tmp_path: Path) -> None:
    testcase_dir, _ = _setup(tmp_path)
    journal_root = journal_root_for(testcase_dir)
    journal_root.mkdir(parents=True)
    broken = journal_root / f"fenceless{JOURNAL_SUFFIX}"
    broken.write_text(
        json.dumps(
            {
                "version": 2,
                "problem_id": PROBLEM_ID,
                "domain": "contest",
                "state": PromotionState.PROMOTED.value,
                "kind": JournalKind.EDIT.value,
                "expected_generation": None,
                "entries": [],
            }
        )
    )

    assert read_journal(broken) is None
    assert await _reconcile(testcase_dir, stored_generation=0) == 0
    assert broken.exists()


@pytest.mark.asyncio
async def test_an_unknown_journal_version_is_left_in_place(tmp_path: Path) -> None:
    testcase_dir, _ = _setup(tmp_path)
    journal_root = journal_root_for(testcase_dir)
    journal_root.mkdir(parents=True)
    future = journal_root / f"future{JOURNAL_SUFFIX}"
    future.write_text(json.dumps({"version": 99, "problem_id": PROBLEM_ID, "domain": "contest", "entries": []}))

    assert read_journal(future) is None
    assert await _reconcile(testcase_dir, stored_generation=0) == 0
    assert future.exists()


@pytest.mark.asyncio
async def test_an_unresolvable_journal_does_not_defer_the_others(tmp_path: Path) -> None:
    """One journal that raises must not strand every journal behind it.

    The failing journal is written **last** and named to sort first, so it comes
    first in the newest-first resolution order. Ordered the other way the test
    would pass even if the handler abandoned everything after the failure, since
    nothing would remain behind it to abandon.
    """
    testcase_dir, _ = _setup(tmp_path)
    journal_root = journal_root_for(testcase_dir)

    write_journal(
        ImportJournal(
            path=journal_root / f"aaa{JOURNAL_SUFFIX}",
            problem_id=PROBLEM_ID,
            domain="contest",
            entries=(),
            state=PromotionState.PROMOTED,
            kind=JournalKind.EDIT,
            expected_generation=1,
        )
    )
    write_journal(
        ImportJournal(
            path=journal_root / f"zzz{JOURNAL_SUFFIX}",
            problem_id="explodes",
            domain="contest",
            entries=(),
            state=PromotionState.PROMOTED,
            kind=JournalKind.EDIT,
            expected_generation=1,
        )
    )
    assert _resolution_order(journal_root)[0].name == f"zzz{JOURNAL_SUFFIX}"

    async def problem_exists(_domain: str, _problem_id: str) -> bool:  # pragma: no cover - edit path
        raise AssertionError("edit journals only")

    async def problem_generation(_domain: str, problem_id: str) -> int | None:
        if problem_id == "explodes":
            raise OSError("the database went away")
        return 5

    resolved = await reconcile_journals(
        journal_root,
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir}),
        problem_generation=problem_generation,
    )

    assert resolved == 1
    assert (journal_root / f"zzz{JOURNAL_SUFFIX}").exists()
    assert not (journal_root / f"aaa{JOURNAL_SUFFIX}").exists()


@pytest.mark.asyncio
async def test_a_version_1_journal_still_reconciles_as_an_import(tmp_path: Path) -> None:
    """Journals written by the previous release must not be stranded by the bump."""
    testcase_dir, _ = _setup(tmp_path)
    journal_root = journal_root_for(testcase_dir)
    journal_root.mkdir(parents=True)
    legacy = journal_root / f"legacy{JOURNAL_SUFFIX}"
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "problem_id": PROBLEM_ID,
                "domain": "contest",
                "state": PromotionState.PROMOTED.value,
                "entries": [
                    {
                        "staged": str(testcase_dir / ".noca-pkg-legacy"),
                        "target": str(testcase_dir / PROBLEM_ID),
                        "root": str(testcase_dir),
                    }
                ],
            }
        )
    )

    journal = read_journal(legacy)
    assert journal is not None
    assert journal.kind is JournalKind.IMPORT
    assert journal.expected_generation is None
    assert journal.entries[0].quarantine is None
    assert journal.entries[0].target_existed is False

    async def problem_exists(_domain: str, _problem_id: str) -> bool:
        return False

    async def problem_generation(_domain: str, _problem_id: str) -> int | None:  # pragma: no cover - import path
        raise AssertionError("an import journal must not consult the generation fence")

    resolved = await reconcile_journals(
        journal_root,
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir}),
        problem_generation=problem_generation,
    )

    assert resolved == 1
    assert not (testcase_dir / PROBLEM_ID).exists()
    assert not legacy.exists()
