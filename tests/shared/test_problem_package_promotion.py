#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for artifact promotion, the import journal, and its reconciliation."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest

from shared.services.problem_package.journal import (
    JOURNAL_SUFFIX,
    ImportJournal,
    JournalEntry,
    PromotionState,
    journal_root_for,
    reconcile_journals,
    write_journal,
)
from shared.services.problem_package.promotion import ArtifactPromoter, commit_with_promotion
from shared.services.problem_package.reader import read_problem_package
from shared.services.sample_problem_package import build_sample_problem_package

PROBLEM_ID = "problem-under-test"


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    """Return the (testcase_dir, statement_dir) pair a Contest import uses."""
    testcase_dir = tmp_path / "testcases" / "contest"
    statement_dir = tmp_path / "statements"
    testcase_dir.mkdir(parents=True)
    statement_dir.mkdir(parents=True)
    return testcase_dir, statement_dir


def _promoter(tmp_path: Path) -> tuple[ArtifactPromoter, Path, Path]:
    testcase_dir, statement_dir = _roots(tmp_path)
    promoter = ArtifactPromoter(
        domain="contest",
        journal_root=journal_root_for(testcase_dir),
        testcase_dir=testcase_dir,
        statement_dir=statement_dir,
    )
    return promoter, testcase_dir, statement_dir


def test_promotion_places_artifacts_and_clears_the_journal(tmp_path: Path) -> None:
    promoter, testcase_dir, statement_dir = _promoter(tmp_path)
    source = build_sample_problem_package(tmp_path / "sample.zip")

    with read_problem_package(source) as staged:
        promoter.stage(staged.package, PROBLEM_ID)
        journal_dir = journal_root_for(testcase_dir)
        assert list(journal_dir.glob(f"*{JOURNAL_SUFFIX}"))  # written before promoting
        promoter.promote()
        promoter.finish()
        promoter.cleanup()

    assert (testcase_dir / PROBLEM_ID / "001.in").read_text() == "1 2\n"
    assert (statement_dir / f"{PROBLEM_ID}-statement.md").exists()
    assert not list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))


def test_rollback_removes_everything_that_was_promoted(tmp_path: Path) -> None:
    promoter, testcase_dir, statement_dir = _promoter(tmp_path)
    source = build_sample_problem_package(tmp_path / "sample.zip")

    with read_problem_package(source) as staged:
        promoter.stage(staged.package, PROBLEM_ID)
        promoter.promote()
        # Stand in for a commit that failed after the artifacts were promoted.
        promoter.rollback()
        promoter.cleanup()

    assert not (testcase_dir / PROBLEM_ID).exists()
    assert not (statement_dir / f"{PROBLEM_ID}-statement.md").exists()
    assert not list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))


def test_cleanup_removes_staged_paths_that_were_never_promoted(tmp_path: Path) -> None:
    promoter, testcase_dir, statement_dir = _promoter(tmp_path)
    source = build_sample_problem_package(tmp_path / "sample.zip")

    with read_problem_package(source) as staged:
        promoter.stage(staged.package, PROBLEM_ID)
        promoter.cleanup()

    leftovers = [path for path in testcase_dir.iterdir() if path.name != journal_root_for(testcase_dir).name]
    assert leftovers == []
    assert list(statement_dir.iterdir()) == []


def _write_stale_journal(
    tmp_path: Path,
    state: PromotionState,
    *,
    problem_id: str = PROBLEM_ID,
    root_override: Path | None = None,
) -> tuple[Path, Path, Path]:
    """Plant a journal describing an import that never committed."""
    testcase_dir, statement_dir = _roots(tmp_path)
    target_dir = testcase_dir / problem_id
    target_dir.mkdir(parents=True)
    (target_dir / "001.in").write_text("1 2\n")
    statement = statement_dir / f"{problem_id}-statement.md"
    statement.write_text("# orphan\n")

    journal_root = journal_root_for(testcase_dir)
    journal = ImportJournal(
        path=journal_root / f"token{JOURNAL_SUFFIX}",
        problem_id=problem_id,
        domain="contest",
        entries=(
            JournalEntry(
                staged=testcase_dir / f".noca-pkg-token-{problem_id}",
                target=target_dir,
                root=root_override or testcase_dir,
            ),
            JournalEntry(
                staged=statement_dir / f".noca-pkg-token-{problem_id}-statement.md",
                target=statement,
                root=root_override or statement_dir,
            ),
        ),
        state=state,
    )
    write_journal(journal)
    return testcase_dir, statement_dir, journal.path


@pytest.mark.parametrize("state", list(PromotionState))
@pytest.mark.asyncio
async def test_reconciliation_removes_orphans_for_every_promotion_state(
    tmp_path: Path,
    state: PromotionState,
) -> None:
    testcase_dir, statement_dir, journal_path = _write_stale_journal(tmp_path, state)

    async def problem_exists(_domain: str, _problem_id: str) -> bool:
        return False

    resolved = await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir, statement_dir}),
    )

    assert resolved == 1
    assert not (testcase_dir / PROBLEM_ID).exists()
    assert not (statement_dir / f"{PROBLEM_ID}-statement.md").exists()
    assert not journal_path.exists()


@pytest.mark.asyncio
async def test_reconciliation_keeps_artifacts_of_a_committed_problem(tmp_path: Path) -> None:
    testcase_dir, statement_dir, journal_path = _write_stale_journal(tmp_path, PromotionState.PROMOTED)

    async def problem_exists(_domain: str, _problem_id: str) -> bool:
        return True

    resolved = await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir, statement_dir}),
    )

    assert resolved == 1
    # The commit won: the artifacts stay and only the journal is cleared.
    assert (testcase_dir / PROBLEM_ID / "001.in").exists()
    assert (statement_dir / f"{PROBLEM_ID}-statement.md").exists()
    assert not journal_path.exists()


@pytest.mark.asyncio
async def test_a_journal_naming_a_path_outside_the_roots_deletes_nothing(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    testcase_dir, statement_dir, _ = _write_stale_journal(tmp_path, PromotionState.PROMOTED, root_override=outside)
    victim = outside / "precious.txt"
    victim.write_text("do not delete me")

    async def problem_exists(_domain: str, _problem_id: str) -> bool:
        return False

    await reconcile_journals(
        journal_root_for(testcase_dir),
        problem_exists=problem_exists,
        allowed_roots=frozenset({testcase_dir, statement_dir}),
    )

    assert victim.exists()
    # The real artifacts are untouched too: an unrecognized root disqualifies the
    # whole entry rather than being second-guessed.
    assert (testcase_dir / PROBLEM_ID / "001.in").exists()


@pytest.mark.asyncio
async def test_an_unreadable_journal_is_left_in_place(tmp_path: Path) -> None:
    testcase_dir, statement_dir = _roots(tmp_path)
    journal_root = journal_root_for(testcase_dir)
    journal_root.mkdir(parents=True)
    broken = journal_root / f"broken{JOURNAL_SUFFIX}"
    broken.write_text("{ not json")

    async def problem_exists(_domain: str, _problem_id: str) -> bool:  # pragma: no cover - never called
        raise AssertionError("an unreadable journal must not be acted on")

    resolved = await reconcile_journals(
        journal_root, problem_exists=problem_exists, allowed_roots=frozenset({testcase_dir, statement_dir})
    )

    assert resolved == 0
    assert broken.exists()


def test_a_process_killed_between_promotion_and_commit_leaves_a_recoverable_journal(tmp_path: Path) -> None:
    """No `raise` is injected into production code: the process is killed instead."""
    script = textwrap.dedent(
        f"""
        import os, signal
        from pathlib import Path
        from shared.services.problem_package.journal import journal_root_for
        from shared.services.problem_package.promotion import ArtifactPromoter
        from shared.services.problem_package.reader import read_problem_package
        from shared.services.sample_problem_package import build_sample_problem_package

        tmp = Path({str(tmp_path)!r})
        testcase_dir = tmp / "testcases" / "contest"
        statement_dir = tmp / "statements"
        testcase_dir.mkdir(parents=True, exist_ok=True)
        statement_dir.mkdir(parents=True, exist_ok=True)

        source = build_sample_problem_package(tmp / "sample.zip")
        promoter = ArtifactPromoter(
            domain="contest",
            journal_root=journal_root_for(testcase_dir),
            testcase_dir=testcase_dir,
            statement_dir=statement_dir,
        )
        with read_problem_package(source) as staged:
            promoter.stage(staged.package, {PROBLEM_ID!r})
            promoter.promote()
            # The commit would happen here. Die instead.
            os.kill(os.getpid(), signal.SIGKILL)
        """
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter, generated script
        [sys.executable, "-c", script],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode != 0, result.stderr.decode()
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()

    testcase_dir = tmp_path / "testcases" / "contest"
    journals = list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))
    assert len(journals) == 1
    payload = json.loads(journals[0].read_text())
    assert payload["state"] == PromotionState.PROMOTED.value
    assert payload["problem_id"] == PROBLEM_ID
    # The orphaned artifacts really are on disk, waiting to be reconciled.
    assert (testcase_dir / PROBLEM_ID / "001.in").exists()


class _PromoterThatCannotRetireItsJournal(ArtifactPromoter):
    """A promoter whose post-commit cleanup fails, e.g. a read-only journal root."""

    def finish(self) -> None:
        """Fail the way a filesystem problem after the commit would."""
        raise OSError("journal directory is read-only")


class _FakeSession:
    """The three session calls :func:`commit_with_promotion` makes."""

    def __init__(self, *, commit_fails: bool = False) -> None:
        self.commit_fails = commit_fails
        self.committed = False
        self.rolled_back = False

    async def flush(self) -> None:
        """Record a flush."""

    async def commit(self) -> None:
        """Commit, or fail on demand."""
        if self.commit_fails:
            raise RuntimeError("commit failed")
        self.committed = True

    async def rollback(self) -> None:
        """Record a rollback."""
        self.rolled_back = True


@pytest.mark.asyncio
async def test_a_failed_commit_deletes_exactly_what_was_promoted(tmp_path: Path) -> None:
    promoter, testcase_dir, statement_dir = _promoter(tmp_path)
    source = build_sample_problem_package(tmp_path / "sample.zip")
    session = _FakeSession(commit_fails=True)

    with read_problem_package(source) as staged, pytest.raises(RuntimeError, match="commit failed"):
        await commit_with_promotion(cast(Any, session), promoter, staged.package, PROBLEM_ID)

    assert session.rolled_back
    assert not (testcase_dir / PROBLEM_ID).exists()
    assert not (statement_dir / f"{PROBLEM_ID}-statement.md").exists()


@pytest.mark.asyncio
async def test_a_failure_after_the_commit_never_deletes_a_committed_problems_files(tmp_path: Path) -> None:
    """The rows are durable, so their files must survive a cleanup failure."""
    promoter, testcase_dir, statement_dir = _promoter(tmp_path)
    promoter = _PromoterThatCannotRetireItsJournal(
        domain=promoter.domain,
        journal_root=promoter.journal_root,
        testcase_dir=promoter.testcase_dir,
        statement_dir=promoter.statement_dir,
    )
    source = build_sample_problem_package(tmp_path / "sample.zip")
    session = _FakeSession()

    with read_problem_package(source) as staged:
        # The post-commit failure is swallowed: it is a cleanup problem, not a
        # reason to fail an import whose data is already durable.
        await commit_with_promotion(cast(Any, session), promoter, staged.package, PROBLEM_ID)

    assert session.committed
    assert not session.rolled_back
    assert (testcase_dir / PROBLEM_ID / "001.in").exists()
    assert (statement_dir / f"{PROBLEM_ID}-statement.md").exists()
    # The journal survives, so reconciliation resolves it against the live row.
    assert list(journal_root_for(testcase_dir).glob(f"*{JOURNAL_SUFFIX}"))
