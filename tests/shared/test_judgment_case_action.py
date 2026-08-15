#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""One immediate judgment action, staged and swapped in.

The endpoints this path replaces committed their rows and wrote their files
afterwards, so a failure between the two left rows describing files that were
never written -- and nothing tested it. These tests assert the property those
endpoints lacked: whatever fails, the directory on disk is either entirely the
old one or entirely the new one.

The database side is a stand-in session, because what is under test is the
*ordering* of filesystem work against the transaction, not the ORM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from shared.services.judgment_case_action import stage_case_action
from shared.services.problem_editor_save import abandon_swap
from shared.services.problem_package.edit_swap import commit_with_edit_swap
from shared.services.problem_save_errors import PendingOpsError
from shared.services.testcase_files import save_testcase_files
from shared.services.testcase_pending_ops import CaseContent, PendingTestCaseOps
from shared.services.testcase_save_plan import CurrentCase

PROBLEM_ID = "acted-on-problem"


class _FakeSession:
    """The calls the action path makes on a session."""

    def __init__(self, *, commit_fails: bool = False, generation: int = 1) -> None:
        self.commit_fails = commit_fails
        self.generation = generation
        self.committed = False
        self.rolled_back = False

    async def scalar(self, _statement: object) -> int:
        """Stand in for the artifact-generation bump."""
        return self.generation

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


def _setup(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    """Create a problem with three cases and return its root and snapshot."""
    root = tmp_path / "contest"
    root.mkdir(parents=True)
    for ordinal, letter in ((1, "a"), (2, "b"), (3, "c")):
        save_testcase_files(PROBLEM_ID, ordinal, f"{letter}-in\n".encode(), f"{letter}-out\n".encode(), root)
    return root, _snapshot(root / PROBLEM_ID)


def _snapshot(directory: Path) -> dict[str, bytes]:
    """Return every file in ``directory`` as ``{name: bytes}``."""
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}


def _current() -> list[CurrentCase]:
    """The three cases as the planner sees them."""
    return [
        CurrentCase(id="a", ordinal=1, is_sample=True),
        CurrentCase(id="b", ordinal=2, is_sample=False),
        CurrentCase(id="c", ordinal=3, is_sample=False),
    ]


async def _act(session: _FakeSession, root: Path, ops: PendingTestCaseOps) -> None:
    """Run one action end to end, as a route does."""
    swap, _materialized = await stage_case_action(
        cast(Any, session),
        domain="contest",
        problem_id=PROBLEM_ID,
        testcase_dir=root,
        current=_current(),
        ops=ops,
        interactive=False,
    )
    await commit_with_edit_swap(cast(Any, session), swap)


_ACTIONS = {
    "delete": PendingTestCaseOps(removals=frozenset({"b"})),
    "replace": PendingTestCaseOps(replacements={"b": CaseContent(input_bytes=b"new-in\n", output_bytes=b"new-out\n")}),
    "add": PendingTestCaseOps(added=(CaseContent(input_bytes=b"add-in\n", output_bytes=b"add-out\n"),)),
    "reorder": PendingTestCaseOps(order=("c", "a", "b")),
    "bulk": PendingTestCaseOps(bulk_cases=(CaseContent(input_bytes=b"only-in\n", output_bytes=b"only-out\n"),)),
}


#: What each action leaves on disk, given the three cases ``_setup`` writes.
_COMMITTED = {
    "delete": {"001.in": b"a-in\n", "001.out": b"a-out\n", "002.in": b"c-in\n", "002.out": b"c-out\n"},
    "replace": {
        "001.in": b"a-in\n",
        "001.out": b"a-out\n",
        "002.in": b"new-in\n",
        "002.out": b"new-out\n",
        "003.in": b"c-in\n",
        "003.out": b"c-out\n",
    },
    "add": {
        "001.in": b"a-in\n",
        "001.out": b"a-out\n",
        "002.in": b"b-in\n",
        "002.out": b"b-out\n",
        "003.in": b"c-in\n",
        "003.out": b"c-out\n",
        "004.in": b"add-in\n",
        "004.out": b"add-out\n",
    },
    "reorder": {
        "001.in": b"c-in\n",
        "001.out": b"c-out\n",
        "002.in": b"a-in\n",
        "002.out": b"a-out\n",
        "003.in": b"b-in\n",
        "003.out": b"b-out\n",
    },
    "bulk": {"001.in": b"only-in\n", "001.out": b"only-out\n"},
}


@pytest.mark.anyio
@pytest.mark.parametrize("action", sorted(_ACTIONS))
async def test_a_committed_action_leaves_exactly_its_own_outcome(tmp_path: Path, action: str) -> None:
    """Dense ordinals, the right content, and no staging leftovers."""
    root, _original = _setup(tmp_path)
    session = _FakeSession()

    await _act(session, root, _ACTIONS[action])

    assert session.committed
    assert _snapshot(root / PROBLEM_ID) == _COMMITTED[action]
    # The journal directory is expected; a leftover staging sibling is not, and a
    # retired journal leaves nothing inside it.
    assert [path.name for path in root.iterdir() if path.name.startswith(".noca-pkg-")] == []
    assert list((root / ".noca-import-journals").iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize("action", sorted(_ACTIONS))
async def test_a_failed_commit_restores_the_previous_directory(tmp_path: Path, action: str) -> None:
    """The property the replaced endpoints did not have."""
    root, original = _setup(tmp_path)
    session = _FakeSession(commit_fails=True)

    with pytest.raises(RuntimeError, match="commit failed"):
        await _act(session, root, _ACTIONS[action])

    assert session.rolled_back
    assert _snapshot(root / PROBLEM_ID) == original


@pytest.mark.anyio
async def test_a_failure_before_promotion_leaves_the_directory_untouched(tmp_path: Path) -> None:
    root, original = _setup(tmp_path)
    session = _FakeSession()

    swap, _materialized = await stage_case_action(
        cast(Any, session),
        domain="contest",
        problem_id=PROBLEM_ID,
        testcase_dir=root,
        current=_current(),
        ops=_ACTIONS["delete"],
        interactive=False,
    )
    await abandon_swap(cast(Any, session), swap)

    assert session.rolled_back
    assert _snapshot(root / PROBLEM_ID) == original
    assert [path.name for path in root.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.anyio
async def test_abandon_swap_finishes_inside_an_already_cancelled_scope(tmp_path: Path) -> None:
    """Cancellation must not interrupt rollback before staging is removed."""
    root, original = _setup(tmp_path)
    session = _FakeSession()
    swap, _materialized = await stage_case_action(
        cast(Any, session),
        domain="contest",
        problem_id=PROBLEM_ID,
        testcase_dir=root,
        current=_current(),
        ops=_ACTIONS["delete"],
        interactive=False,
    )

    with anyio.CancelScope() as scope:
        scope.cancel()
        await abandon_swap(cast(Any, session), swap)

    assert session.rolled_back
    assert _snapshot(root / PROBLEM_ID) == original
    assert [path.name for path in root.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.anyio
async def test_a_delete_renumbers_the_survivors_before_the_commit(tmp_path: Path) -> None:
    """Renumbering used to run after the commit, where a failure shifted the set."""
    root, _original = _setup(tmp_path)

    await _act(_FakeSession(), root, _ACTIONS["delete"])

    assert _snapshot(root / PROBLEM_ID) == {
        "001.in": b"a-in\n",
        "001.out": b"a-out\n",
        "002.in": b"c-in\n",
        "002.out": b"c-out\n",
    }


@pytest.mark.anyio
async def test_a_reorder_moves_content_with_the_ordinals(tmp_path: Path) -> None:
    root, _original = _setup(tmp_path)

    await _act(_FakeSession(), root, _ACTIONS["reorder"])

    assert _snapshot(root / PROBLEM_ID)["001.in"] == b"c-in\n"
    assert _snapshot(root / PROBLEM_ID)["002.in"] == b"a-in\n"
    assert _snapshot(root / PROBLEM_ID)["003.in"] == b"b-in\n"


@pytest.mark.anyio
async def test_a_bulk_replacement_does_not_seed_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Its plan claims nothing from the current files, so linking them is waste."""
    root, _original = _setup(tmp_path)
    seeded = False

    import shared.services.problem_package.edit_swap as edit_swap

    def _record(*args: object, **kwargs: object) -> int:
        nonlocal seeded
        seeded = True
        return 0

    monkeypatch.setattr(edit_swap, "copy_testcase_files_into", _record)

    await _act(_FakeSession(), root, _ACTIONS["bulk"])

    assert seeded is False
    assert _snapshot(root / PROBLEM_ID) == {"001.in": b"only-in\n", "001.out": b"only-out\n"}


@pytest.mark.anyio
async def test_a_staging_failure_leaves_nothing_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Staging writes the problem's whole test data, so a leak is measured in gigabytes.

    The caller cannot clean up a swap it was never handed: if staging raises, the
    return never happens and `abandon_swap` has nothing to be called on. So the
    staging step owns that cleanup itself.
    """
    root, original = _setup(tmp_path)

    import shared.services.problem_editor_save as editor_save

    def _explode(*args: object, **kwargs: object) -> list[Any]:
        raise OSError("no space left on device")

    # Fails *after* staging exists and has been seeded, which is the leak: the
    # directory is on disk, holding a link or a copy of every case, and nobody
    # holds a reference that could remove it.
    monkeypatch.setattr(editor_save, "materialize", _explode)
    session = _FakeSession()

    with pytest.raises(OSError, match="no space left"):
        await stage_case_action(
            cast(Any, session),
            domain="contest",
            problem_id=PROBLEM_ID,
            testcase_dir=root,
            current=_current(),
            ops=_ACTIONS["add"],
            interactive=False,
        )

    assert _snapshot(root / PROBLEM_ID) == original
    leftovers = [path.name for path in root.iterdir() if path.name.startswith(".noca-pkg-")]
    assert leftovers == []


@pytest.mark.anyio
async def test_an_action_naming_a_vanished_case_is_refused(tmp_path: Path) -> None:
    """A route validates its target before it can take the lock, so it may be gone.

    Planning simply never matches an id the problem no longer has, which makes a
    delete delete nothing and a replacement replace nothing -- and both report
    success. The author is told to reload instead.
    """
    root, original = _setup(tmp_path)
    survivors = [case for case in _current() if case.id != "b"]

    with pytest.raises(PendingOpsError, match="no longer exists"):
        await stage_case_action(
            cast(Any, _FakeSession()),
            domain="contest",
            problem_id=PROBLEM_ID,
            testcase_dir=root,
            current=survivors,
            ops=PendingTestCaseOps(removals=frozenset({"b"})),
            interactive=False,
        )

    assert _snapshot(root / PROBLEM_ID) == original
    assert [path.name for path in root.iterdir() if path.name.startswith(".noca-pkg-")] == []


@pytest.mark.anyio
async def test_a_reorder_naming_a_vanished_case_is_refused(tmp_path: Path) -> None:
    """A drag carries the order the browser last saw, which a concurrent delete broke."""
    root, original = _setup(tmp_path)
    survivors = [case for case in _current() if case.id != "c"]

    with pytest.raises(PendingOpsError, match="no longer exists"):
        await stage_case_action(
            cast(Any, _FakeSession()),
            domain="contest",
            problem_id=PROBLEM_ID,
            testcase_dir=root,
            current=survivors,
            ops=PendingTestCaseOps(order=("c", "a", "b")),
            interactive=False,
        )

    assert _snapshot(root / PROBLEM_ID) == original
