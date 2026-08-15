#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the edit-aware artifact swap and its four failure points.

Every test starts from a problem that *already has* test data, because that is
the whole difference from an import: there is content to lose. The assertions are
therefore about bytes — the recovered directory is compared file by file against
a snapshot taken before the Save.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from shared.services.problem_package.edit_swap import EditArtifactSwap, commit_with_edit_swap
from shared.services.problem_package.journal import (
    JOURNAL_SUFFIX,
    JournalKind,
    PromotionState,
    journal_root_for,
    reconcile_journals,
)
from shared.services.testcase_files import delete_testcase_files_in, write_testcase_files_into

PROBLEM_ID = "problem-under-test"

#: The two strategies differ on disk by exactly one thing: an interactive problem's
#: test cases carry input only, so a lost ``.out`` is not what would be noticed.
STRATEGIES = ("standard", "interactive")


def _existing_files(strategy: str) -> dict[str, bytes]:
    """Return the test-case files a problem of this strategy starts with."""
    files = {"001.in": b"1 2\n", "002.in": b"3 4\n"}
    if strategy == "standard":
        files |= {"001.out": b"3\n", "002.out": b"7\n"}
    return files


def _setup(tmp_path: Path, strategy: str) -> tuple[Path, dict[str, bytes]]:
    """Create a test-case root holding one problem's existing files."""
    testcase_dir = tmp_path / "testcases" / "contest"
    problem_dir = testcase_dir / PROBLEM_ID
    problem_dir.mkdir(parents=True)
    files = _existing_files(strategy)
    for name, content in files.items():
        (problem_dir / name).write_bytes(content)
    return testcase_dir, files


def _snapshot(directory: Path) -> dict[str, bytes]:
    """Return every file in ``directory`` as ``{name: bytes}``."""
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}


def _swap(testcase_dir: Path, *, expected_generation: int = 1) -> EditArtifactSwap:
    return EditArtifactSwap(
        domain="contest",
        journal_root=journal_root_for(testcase_dir),
        testcase_dir=testcase_dir,
        problem_id=PROBLEM_ID,
        expected_generation=expected_generation,
    )


def _apply_edit(staged: Path, strategy: str) -> None:
    """Apply a representative delta: rewrite case 1, drop case 2, add case 3."""
    write_testcase_files_into(staged, 1, b"9 9\n", b"18\n" if strategy == "standard" else None)
    delete_testcase_files_in(staged, 2)
    write_testcase_files_into(staged, 3, b"5 5\n", b"10\n" if strategy == "standard" else None)


class _FakeSession:
    """The three session calls :func:`commit_with_edit_swap` makes."""

    def __init__(self, *, commit_fails: bool = False) -> None:
        self.commit_fails = commit_fails
        self.committed = False
        self.rolled_back = False
        #: What a failing commit raises. Cancellation is a `BaseException`, which
        #: is the case the shielded undo exists for.
        self.commit_error: type[BaseException] = RuntimeError

    async def flush(self) -> None:
        """Record a flush."""

    async def commit(self) -> None:
        """Commit, or fail on demand."""
        if self.commit_fails:
            raise self.commit_error("commit failed")
        self.committed = True

    async def rollback(self) -> None:
        """Record a rollback."""
        self.rolled_back = True


async def _reconcile(testcase_dir: Path, stored_generation: int | None, *, in_flight: bool = False) -> int:
    """Run reconciliation with a stored generation standing in for the database.

    ``in_flight`` stands in for the row lock a running Save holds: in production
    the guard is a ``SELECT ... FOR UPDATE SKIP LOCKED`` against the problem row,
    held for the whole resolution, which this suite's SQLite engine cannot
    express.
    """

    async def problem_exists(_domain: str, _problem_id: str) -> bool:  # pragma: no cover - edit path
        raise AssertionError("an edit journal must not be resolved on problem existence")

    async def problem_generation(_domain: str, _problem_id: str) -> int | None:
        return stored_generation

    @asynccontextmanager
    async def edit_guard(_domain: str, _problem_id: str) -> AsyncIterator[bool]:
        yield not in_flight

    return await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir}),
        problem_generation=problem_generation,
        edit_guard=edit_guard,
    )


# --------------------------------------------------------------------------- #
# Failure point 1: before promotion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_a_failure_before_promotion_leaves_durable_state_untouched(tmp_path: Path, strategy: str) -> None:
    testcase_dir, original = _setup(tmp_path, strategy)
    swap = _swap(testcase_dir)

    staged = swap.stage_test_cases()
    _apply_edit(staged, strategy)
    # Stand in for a staging step that raised before anything was promoted.
    swap.cleanup()

    assert _snapshot(testcase_dir / PROBLEM_ID) == original
    assert not staged.exists()
    assert not list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))


# --------------------------------------------------------------------------- #
# Failure point 2: between promotion and commit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_a_crash_between_promotion_and_commit_restores_the_previous_files(
    tmp_path: Path,
    strategy: str,
) -> None:
    """No `raise` is injected into production code: the process is killed instead."""
    testcase_dir, original = _setup(tmp_path, strategy)
    script = textwrap.dedent(
        f"""
        import os, signal
        from pathlib import Path
        from shared.services.problem_package.edit_swap import EditArtifactSwap
        from shared.services.problem_package.journal import journal_root_for
        from shared.services.testcase_files import delete_testcase_files_in, write_testcase_files_into

        testcase_dir = Path({str(testcase_dir)!r})
        swap = EditArtifactSwap(
            domain="contest",
            journal_root=journal_root_for(testcase_dir),
            testcase_dir=testcase_dir,
            problem_id={PROBLEM_ID!r},
            expected_generation=1,
        )
        staged = swap.stage_test_cases()
        write_testcase_files_into(staged, 1, b"9 9\\n", {"b'18\\\\n'" if strategy == "standard" else "None"})
        delete_testcase_files_in(staged, 2)
        swap.write_journal()
        swap.promote()
        # The commit would happen here. Die instead.
        os.kill(os.getpid(), signal.SIGKILL)
        """
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter, generated script
        [sys.executable, "-c", script],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()

    # The promotion really did land: the new content is live and the old is parked.
    assert _snapshot(testcase_dir / PROBLEM_ID) != original
    journals = list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))
    assert len(journals) == 1
    payload = json.loads(journals[0].read_text())
    assert payload["kind"] == JournalKind.EDIT.value
    assert payload["expected_generation"] == 1
    assert payload["state"] == PromotionState.PROMOTED.value

    # The row never reached generation 1, so recovery puts the author's files back.
    resolved = await _reconcile(testcase_dir, stored_generation=0)

    assert resolved == 1
    assert _snapshot(testcase_dir / PROBLEM_ID) == original
    assert not journals[0].exists()
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


# --------------------------------------------------------------------------- #
# Failure point 3: during commit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_a_failed_commit_restores_the_previous_files_exactly(tmp_path: Path, strategy: str) -> None:
    testcase_dir, original = _setup(tmp_path, strategy)
    swap = _swap(testcase_dir)
    session = _FakeSession(commit_fails=True)

    staged = swap.stage_test_cases()
    _apply_edit(staged, strategy)
    with pytest.raises(RuntimeError, match="commit failed"):
        await commit_with_edit_swap(cast(Any, session), swap)

    assert session.rolled_back
    assert _snapshot(testcase_dir / PROBLEM_ID) == original
    assert not list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.asyncio
async def test_a_successful_save_swaps_the_directory_and_retires_the_journal(tmp_path: Path) -> None:
    testcase_dir, _ = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)
    session = _FakeSession()

    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    await commit_with_edit_swap(cast(Any, session), swap)

    assert session.committed
    assert _snapshot(testcase_dir / PROBLEM_ID) == {
        "001.in": b"9 9\n",
        "001.out": b"18\n",
        "003.in": b"5 5\n",
        "003.out": b"10\n",
    }
    assert not list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


# --------------------------------------------------------------------------- #
# Failure point 4: after commit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("stored_generation", [1, 2])
@pytest.mark.asyncio
async def test_a_crash_after_the_commit_keeps_the_new_artifacts(tmp_path: Path, stored_generation: int) -> None:
    """A generation at or beyond the fence means the rows are durable; never roll back.

    ``2`` is the superseded case: a later Save committed on top, so the quarantined
    predecessor is doubly stale and restoring it would clobber live content.
    """
    testcase_dir, original = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)

    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    swap.write_journal()
    swap.promote()
    promoted = _snapshot(testcase_dir / PROBLEM_ID)
    assert promoted != original

    resolved = await _reconcile(testcase_dir, stored_generation=stored_generation)

    assert resolved == 1
    assert _snapshot(testcase_dir / PROBLEM_ID) == promoted
    assert not list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.asyncio
async def test_an_edit_journal_whose_problem_vanished_removes_its_artifacts(tmp_path: Path) -> None:
    testcase_dir, _ = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)

    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    swap.write_journal()
    swap.promote()

    resolved = await _reconcile(testcase_dir, stored_generation=None)

    assert resolved == 1
    assert not (testcase_dir / PROBLEM_ID).exists()
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


# --------------------------------------------------------------------------- #
# The recovery inferences themselves
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_a_crash_between_the_journal_and_the_promotion_keeps_the_original(
    tmp_path: Path,
    strategy: str,
) -> None:
    """The journal names a quarantine that was never created; the target is the original.

    Recovery must read the *disk* here. Treating a planned-but-absent quarantine
    as "nothing to restore" and deleting the target would destroy the author's
    files at the one moment they are the only copy.
    """
    testcase_dir, original = _setup(tmp_path, strategy)
    script = textwrap.dedent(
        f"""
        import os, signal
        from pathlib import Path
        from shared.services.problem_package.edit_swap import EditArtifactSwap
        from shared.services.problem_package.journal import journal_root_for

        testcase_dir = Path({str(testcase_dir)!r})
        swap = EditArtifactSwap(
            domain="contest",
            journal_root=journal_root_for(testcase_dir),
            testcase_dir=testcase_dir,
            problem_id={PROBLEM_ID!r},
            expected_generation=1,
        )
        swap.stage_test_cases()
        swap.write_journal()
        # Die between journalling and promoting.
        os.kill(os.getpid(), signal.SIGKILL)
        """
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter, generated script
        [sys.executable, "-c", script],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()
    assert _snapshot(testcase_dir / PROBLEM_ID) == original

    resolved = await _reconcile(testcase_dir, stored_generation=0)

    assert resolved == 1
    assert _snapshot(testcase_dir / PROBLEM_ID) == original
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_a_failure_midway_through_promotion_restores_the_original(tmp_path: Path, strategy: str) -> None:
    """The window between the two renames must not strand the quarantined original."""
    testcase_dir, original = _setup(tmp_path, strategy)
    swap = _swap(testcase_dir)
    staged = swap.stage_test_cases()
    _apply_edit(staged, strategy)
    swap.write_journal()

    # Destroy the staged directory so the *second* rename fails after the first
    # one has already moved the author's files into quarantine.
    shutil.rmtree(staged)
    with pytest.raises(OSError):
        swap.promote()
    swap.rollback()
    swap.cleanup()

    assert _snapshot(testcase_dir / PROBLEM_ID) == original
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.asyncio
async def test_a_multi_entry_save_restores_every_artifact(tmp_path: Path) -> None:
    """A Save spanning both roots rolls back all of it, not just the last entry."""
    testcase_dir, original = _setup(tmp_path, "standard")
    statement_dir = tmp_path / "statements"
    statement_dir.mkdir()
    statement = statement_dir / f"{PROBLEM_ID}-statement.md"
    statement.write_bytes(b"# original\n")

    swap = _swap(testcase_dir)
    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    swap.stage_file(b"# replacement\n", statement, statement_dir)
    session = _FakeSession(commit_fails=True)

    with pytest.raises(RuntimeError, match="commit failed"):
        await commit_with_edit_swap(cast(Any, session), swap)

    assert _snapshot(testcase_dir / PROBLEM_ID) == original
    assert statement.read_bytes() == b"# original\n"
    assert [path.name for path in statement_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.asyncio
async def test_recovery_is_safe_to_run_twice(tmp_path: Path) -> None:
    """A reconciliation pass interrupted and re-run must converge, not compound."""
    testcase_dir, original = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)
    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    swap.write_journal()
    swap.promote()
    journal = next(iter(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}")))
    payload = journal.read_bytes()

    assert await _reconcile(testcase_dir, stored_generation=0) == 1
    assert _snapshot(testcase_dir / PROBLEM_ID) == original

    # Replay the same journal, as a crash before it was erased would.
    journal.write_bytes(payload)
    assert await _reconcile(testcase_dir, stored_generation=0) == 1
    assert _snapshot(testcase_dir / PROBLEM_ID) == original


@pytest.mark.asyncio
async def test_two_lost_saves_restore_to_the_true_original(tmp_path: Path) -> None:
    """The complete chain is undone newest-first while one guard owns it."""
    testcase_dir, original = _setup(tmp_path, "standard")

    first = _swap(testcase_dir)
    staged = first.stage_test_cases()
    write_testcase_files_into(staged, 1, b"first save\n", b"1\n")
    first.write_journal()
    first.promote()

    second = _swap(testcase_dir)
    staged = second.stage_test_cases()
    write_testcase_files_into(staged, 1, b"second save\n", b"2\n")
    second.write_journal()
    second.promote()

    assert len(list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))) == 2

    events: list[str] = []

    async def problem_exists(_domain: str, _problem_id: str) -> bool:  # pragma: no cover - edit path
        raise AssertionError("an edit journal must not be resolved on problem existence")

    async def problem_generation(_domain: str, _problem_id: str) -> int | None:
        events.append("generation")
        return 0

    @asynccontextmanager
    async def edit_guard(_domain: str, _problem_id: str) -> AsyncIterator[bool]:
        events.append("enter")
        try:
            yield True
        finally:
            events.append("exit")

    resolved = await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir}),
        problem_generation=problem_generation,
        edit_guard=edit_guard,
    )

    assert resolved == 2
    assert events == ["enter", "generation", "generation", "exit"]
    assert _snapshot(testcase_dir / PROBLEM_ID) == original


@pytest.mark.asyncio
async def test_content_appearing_after_the_journal_is_still_restored(tmp_path: Path) -> None:
    """The journaled existence snapshot can be stale; the quarantine on disk cannot.

    A problem with no files on disk journals ``target_existed=False``. A satellite
    test-case route — still writing directly into the live directory until those
    routes are deleted — then creates content before the promotion runs, so the
    promotion quarantines something the journal said would not be there. Recovery
    must restore it. Deciding from the stale snapshot instead would delete the
    author's only copy.
    """
    testcase_dir = tmp_path / "testcases" / "contest"
    testcase_dir.mkdir(parents=True)
    swap = _swap(testcase_dir)

    staged = swap.stage_test_cases()
    write_testcase_files_into(staged, 1, b"saved edit\n", b"1\n")
    swap.write_journal()

    # The satellite write lands between journalling and promotion.
    satellite = {"001.in": b"satellite\n", "001.out": b"9\n"}
    (testcase_dir / PROBLEM_ID).mkdir(parents=True)
    for name, content in satellite.items():
        (testcase_dir / PROBLEM_ID / name).write_bytes(content)

    swap.promote()
    assert _snapshot(testcase_dir / PROBLEM_ID) != satellite

    resolved = await _reconcile(testcase_dir, stored_generation=0)

    assert resolved == 1
    assert _snapshot(testcase_dir / PROBLEM_ID) == satellite
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.asyncio
async def test_content_promoted_over_nothing_is_removed_when_the_commit_is_lost(tmp_path: Path) -> None:
    """The other half of the rule: with nothing displaced, there is nothing to keep."""
    testcase_dir = tmp_path / "testcases" / "contest"
    testcase_dir.mkdir(parents=True)
    swap = _swap(testcase_dir)

    staged = swap.stage_test_cases()
    write_testcase_files_into(staged, 1, b"first ever case\n", b"1\n")
    swap.write_journal()
    swap.promote()
    assert (testcase_dir / PROBLEM_ID / "001.in").exists()

    resolved = await _reconcile(testcase_dir, stored_generation=0)

    assert resolved == 1
    # The rows describe a problem with no test cases, so neither may the disk.
    assert not (testcase_dir / PROBLEM_ID).exists()
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


def test_promoting_without_a_journal_is_refused(tmp_path: Path) -> None:
    """Promoting unjournaled reopens the one window the journal exists to close."""
    testcase_dir, original = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)
    swap.stage_test_cases()

    with pytest.raises(RuntimeError, match="write_journal"):
        swap.promote()

    assert _snapshot(testcase_dir / PROBLEM_ID) == original


# --------------------------------------------------------------------------- #
# Removals: a Save that ends with less on disk than it started with
# --------------------------------------------------------------------------- #


def _statement_root(tmp_path: Path) -> Path:
    """Create a statement root holding both formats for one problem."""
    root = tmp_path / "statements"
    root.mkdir(parents=True)
    (root / f"{PROBLEM_ID}.pdf").write_bytes(b"%PDF-old\n")
    return root


@pytest.mark.anyio
async def test_a_committed_removal_deletes_the_file_and_its_quarantine(tmp_path: Path) -> None:
    """Switching statement format drops the other file once the Save commits."""
    testcase_dir, _original = _setup(tmp_path, "standard")
    statement_root = _statement_root(tmp_path)
    swap = _swap(testcase_dir)
    swap.stage_test_cases()
    swap.stage_file(b"# markdown\n", statement_root / f"{PROBLEM_ID}.md", statement_root)
    swap.stage_removal(statement_root / f"{PROBLEM_ID}.pdf", statement_root)
    session = _FakeSession()

    await commit_with_edit_swap(cast(Any, session), swap)

    assert session.committed
    assert (statement_root / f"{PROBLEM_ID}.md").read_bytes() == b"# markdown\n"
    assert not (statement_root / f"{PROBLEM_ID}.pdf").exists()
    assert [path.name for path in statement_root.iterdir()] == [f"{PROBLEM_ID}.md"]


@pytest.mark.anyio
async def test_a_failed_commit_puts_a_removed_file_back(tmp_path: Path) -> None:
    """The removal is as reversible as a replacement, or the Save loses both files."""
    testcase_dir, original = _setup(tmp_path, "standard")
    statement_root = _statement_root(tmp_path)
    swap = _swap(testcase_dir)
    swap.stage_test_cases()
    swap.stage_file(b"# markdown\n", statement_root / f"{PROBLEM_ID}.md", statement_root)
    swap.stage_removal(statement_root / f"{PROBLEM_ID}.pdf", statement_root)
    session = _FakeSession(commit_fails=True)

    with pytest.raises(RuntimeError, match="commit failed"):
        await commit_with_edit_swap(cast(Any, session), swap)

    assert (statement_root / f"{PROBLEM_ID}.pdf").read_bytes() == b"%PDF-old\n"
    assert not (statement_root / f"{PROBLEM_ID}.md").exists()
    assert _snapshot(testcase_dir / PROBLEM_ID) == original


@pytest.mark.anyio
async def test_a_lost_commit_restores_a_removed_file_at_startup(tmp_path: Path) -> None:
    """Recovery resolves a removal from the quarantine on disk, like any entry."""
    testcase_dir, _original = _setup(tmp_path, "standard")
    statement_root = _statement_root(tmp_path)
    swap = _swap(testcase_dir, expected_generation=4)
    swap.stage_test_cases()
    swap.stage_removal(statement_root / f"{PROBLEM_ID}.pdf", statement_root)
    swap.write_journal()
    swap.promote()
    assert not (statement_root / f"{PROBLEM_ID}.pdf").exists()

    async def problem_exists(_domain: str, _problem_id: str) -> bool:  # pragma: no cover - edit path
        raise AssertionError("an edit journal must not be resolved on problem existence")

    async def problem_generation(_domain: str, _problem_id: str) -> int | None:
        return 3

    resolved = await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir, statement_root}),
        problem_generation=problem_generation,
    )

    assert resolved == 1
    assert (statement_root / f"{PROBLEM_ID}.pdf").read_bytes() == b"%PDF-old\n"


@pytest.mark.anyio
async def test_a_removal_journal_without_the_optional_key_still_resolves(tmp_path: Path) -> None:
    """The ``removal`` key is additive: recovery decides from the quarantine."""
    testcase_dir, _original = _setup(tmp_path, "standard")
    statement_root = _statement_root(tmp_path)
    swap = _swap(testcase_dir, expected_generation=2)
    swap.stage_test_cases()
    swap.stage_removal(statement_root / f"{PROBLEM_ID}.pdf", statement_root)
    swap.write_journal()
    swap.promote()

    journal_file = next(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))
    payload = json.loads(journal_file.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        entry.pop("removal", None)
    journal_file.write_text(json.dumps(payload), encoding="utf-8")

    async def problem_exists(_domain: str, _problem_id: str) -> bool:  # pragma: no cover - edit path
        raise AssertionError("an edit journal must not be resolved on problem existence")

    async def problem_generation(_domain: str, _problem_id: str) -> int | None:
        return 1

    resolved = await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir, statement_root}),
        problem_generation=problem_generation,
    )

    assert resolved == 1
    assert (statement_root / f"{PROBLEM_ID}.pdf").read_bytes() == b"%PDF-old\n"


# --------------------------------------------------------------------------- #
# A journal whose Save is still running is not evidence of anything
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reconciliation_leaves_a_live_saves_journal_alone(tmp_path: Path) -> None:
    """Between promotion and commit, a live Save looks exactly like a lost one.

    Its journal is on disk, its content is promoted, and the row still holds the
    previous generation -- because its transaction has not committed. Resolving
    it there would restore the quarantined originals underneath the Save, which
    would then commit rows describing files that had been moved back. The
    pre-import pass runs while the application is serving, so this is reachable
    without a crash: an import of one problem reconciles the journals of all of
    them.
    """
    testcase_dir, original = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)
    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    swap.write_journal()
    swap.promote()
    promoted = _snapshot(testcase_dir / PROBLEM_ID)
    assert promoted != original

    resolved = await _reconcile(testcase_dir, stored_generation=0, in_flight=True)

    assert resolved == 0
    assert _snapshot(testcase_dir / PROBLEM_ID) == promoted
    assert len(list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))) == 1

    # And once the Save is gone, the very same journal resolves as before.
    assert await _reconcile(testcase_dir, stored_generation=0) == 1
    assert _snapshot(testcase_dir / PROBLEM_ID) == original


def test_promotion_flushes_the_directory_entries_it_rewrote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rename is atomic, but not durable until its directory is flushed.

    Without this, a host crash can leave the database holding the committed
    generation while the promoted directory entry is gone -- and recovery reads
    that generation as "the Save landed, keep the new artifacts".
    """
    testcase_dir, _original = _setup(tmp_path, "standard")
    flushed: list[str] = []
    real = os.fsync

    def _record(fd: int) -> None:
        try:
            flushed.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:  # pragma: no cover - non-Linux fallback
            flushed.append("")
        real(fd)

    swap = _swap(testcase_dir)
    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    swap.write_journal()
    monkeypatch.setattr(os, "fsync", _record)

    swap.promote()

    assert str(testcase_dir) in flushed, "the promoted directory's parent was never flushed"


@pytest.mark.asyncio
async def test_reconciliation_holds_the_problem_for_the_whole_resolution(tmp_path: Path) -> None:
    """Asking whether a Save is running, then acting, are two separate moments.

    A Save that starts in between would be trampled by a recovery that had already
    decided the coast was clear, so ownership has to span the fence lookup, the
    file moves and the journal's removal. This records when the guard is entered
    and exited around those steps.
    """
    testcase_dir, original = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)
    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    swap.write_journal()
    swap.promote()
    events: list[str] = []

    async def problem_exists(_domain: str, _problem_id: str) -> bool:  # pragma: no cover - edit path
        raise AssertionError("an edit journal must not be resolved on problem existence")

    async def problem_generation(_domain: str, _problem_id: str) -> int | None:
        events.append("generation")
        return 0

    @asynccontextmanager
    async def edit_guard(_domain: str, _problem_id: str) -> AsyncIterator[bool]:
        events.append("enter")
        try:
            yield True
        finally:
            events.append("exit")

    resolved = await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir}),
        problem_generation=problem_generation,
        edit_guard=edit_guard,
    )

    assert resolved == 1
    assert events == ["enter", "generation", "exit"]
    # And the resolution really happened inside it.
    assert _snapshot(testcase_dir / PROBLEM_ID) == original
    assert list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}")) == []


def test_a_cancelled_commit_still_restores_the_authors_files(tmp_path: Path) -> None:
    """Cancellation is a `BaseException`, and the rollback is a sequence of renames.

    A cancel delivered partway through would leave the problem half restored --
    some cases the Save's, some the author's -- which is the one outcome the
    quarantine exists to make impossible, so the undo runs shielded.
    """
    testcase_dir, original = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)
    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    session = cast(Any, _FakeSession(commit_fails=True))

    async def _run() -> None:
        # The cancellation class is backend-specific, so it is read inside the
        # loop and raised from the commit exactly as a real cancel would be.
        session.commit_error = anyio.get_cancelled_exc_class()
        await commit_with_edit_swap(session, swap)

    with pytest.raises(BaseException):  # noqa: B017 - the cancellation class varies by backend
        anyio.run(_run)

    assert _snapshot(testcase_dir / PROBLEM_ID) == original
    assert [path.name for path in testcase_dir.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.asyncio
async def test_recovery_flushes_the_restoration_before_clearing_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journal is deleted immediately after the restore, so the restore must be durable.

    A second crash could otherwise persist the deletion while losing the renames it
    recorded, leaving the problem with an uncommitted Save's files and nothing left
    on disk to say so.
    """
    testcase_dir, _original = _setup(tmp_path, "standard")
    swap = _swap(testcase_dir)
    staged = swap.stage_test_cases()
    _apply_edit(staged, "standard")
    swap.write_journal()
    swap.promote()
    flushed: list[str] = []
    real = os.fsync

    def _record(fd: int) -> None:
        try:
            flushed.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:  # pragma: no cover - non-Linux fallback
            flushed.append("")
        real(fd)

    monkeypatch.setattr(os, "fsync", _record)

    assert await _reconcile(testcase_dir, stored_generation=0) == 1

    assert str(testcase_dir) in flushed, "the restored directory's parent was never flushed"
