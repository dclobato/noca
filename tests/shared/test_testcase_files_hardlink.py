#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Staging is seeded with hardlinks, and nothing may write through one.

Linking is what keeps an editor action O(number of cases) instead of O(total
bytes) -- a problem may hold gigabytes and every action stages the whole
directory. It is only safe while every write goes to a temporary name and is
renamed over its target: writing in place would reach the live file through the
link and corrupt exactly the data the staged swap exists to protect.

So these tests assert the invariant on the live inode, not merely on the bytes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from shared.services import testcase_files
from shared.services.testcase_files import (
    copy_testcase_files_into,
    delete_testcase_files_in,
    get_problem_testcase_dir,
    save_testcase_files,
    write_testcase_files_into,
)

PROBLEM_ID = "linked-problem"


@pytest.fixture(autouse=True)
def _forget_capability_cache() -> None:
    """Each test decides for itself whether the root supports links."""
    testcase_files._NO_HARDLINK_ROOTS.clear()


def _seed_problem(root: Path) -> Path:
    """Create one problem with two cases and return its live directory."""
    live = get_problem_testcase_dir(PROBLEM_ID, root)
    live.mkdir(parents=True, exist_ok=True)
    save_testcase_files(PROBLEM_ID, 1, b"one-in\n", b"one-out\n", root)
    save_testcase_files(PROBLEM_ID, 2, b"two-in\n", b"two-out\n", root)
    return live


def test_seeding_links_rather_than_copying(tmp_path: Path) -> None:
    live = _seed_problem(tmp_path)
    staged = tmp_path / "staged"

    seeded = copy_testcase_files_into(PROBLEM_ID, tmp_path, staged)

    assert seeded == 4
    assert (staged / "001.in").stat().st_ino == (live / "001.in").stat().st_ino


def test_writing_a_staged_case_never_touches_the_live_file(tmp_path: Path) -> None:
    """The invariant the whole scheme rests on."""
    live = _seed_problem(tmp_path)
    staged = tmp_path / "staged"
    copy_testcase_files_into(PROBLEM_ID, tmp_path, staged)
    live_inode = (live / "001.in").stat().st_ino

    write_testcase_files_into(staged, 1, b"replaced\n", b"replaced-out\n")

    assert (live / "001.in").read_bytes() == b"one-in\n"
    assert (live / "001.in").stat().st_ino == live_inode
    assert (staged / "001.in").read_bytes() == b"replaced\n"
    assert (staged / "001.in").stat().st_ino != live_inode


def test_deleting_a_staged_case_never_touches_the_live_file(tmp_path: Path) -> None:
    live = _seed_problem(tmp_path)
    staged = tmp_path / "staged"
    copy_testcase_files_into(PROBLEM_ID, tmp_path, staged)

    delete_testcase_files_in(staged, 1)

    assert not (staged / "001.in").exists()
    assert (live / "001.in").read_bytes() == b"one-in\n"


def test_renaming_inside_staging_never_touches_the_live_file(tmp_path: Path) -> None:
    """Reordering renames links; the inodes they point at are untouched."""
    live = _seed_problem(tmp_path)
    staged = tmp_path / "staged"
    copy_testcase_files_into(PROBLEM_ID, tmp_path, staged)

    (staged / "001.in").rename(staged / "003.in")

    assert (live / "001.in").read_bytes() == b"one-in\n"
    assert (staged / "003.in").read_bytes() == b"one-in\n"


def test_a_root_without_hardlinks_falls_back_to_copying(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FAT/exFAT, some network shares and some bind mounts refuse links."""
    live = _seed_problem(tmp_path)
    staged = tmp_path / "staged"

    def _refuse(source: str | Path, target: str | Path) -> None:
        raise OSError("this filesystem does not support hardlinks")

    monkeypatch.setattr(os, "link", _refuse)

    seeded = copy_testcase_files_into(PROBLEM_ID, tmp_path, staged)

    assert seeded == 4
    assert (staged / "001.in").read_bytes() == (live / "001.in").read_bytes()
    assert (staged / "001.in").stat().st_ino != (live / "001.in").stat().st_ino


def test_the_fallback_is_decided_once_per_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe result is cached, so one warning is logged rather than one per file."""
    _seed_problem(tmp_path)
    attempts = 0

    def _refuse(source: str | Path, target: str | Path) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("nope")

    monkeypatch.setattr(os, "link", _refuse)

    copy_testcase_files_into(PROBLEM_ID, tmp_path, tmp_path / "first")
    copy_testcase_files_into(PROBLEM_ID, tmp_path, tmp_path / "second")

    assert attempts == 1


def test_writing_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    staged.mkdir()

    write_testcase_files_into(staged, 1, b"in\n", b"out\n")

    assert sorted(path.name for path in staged.iterdir()) == ["001.in", "001.out"]
