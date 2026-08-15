#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The editor's crash-safety argument depends on the writes actually being durable.

Recovery compares what is on disk against a committed ``artifact_generation``.
That comparison is only meaningful if the files reached the device before the
transaction committed: content still sitting in the kernel's page cache when the
host loses power leaves a committed row describing a file that is not there --
the one state recovery cannot detect, because a generation at or beyond the fence
tells it to *keep* the new artifacts.

A crash is not reproducible in a unit test, so these assert the calls that make
the difference: the data is flushed before the rename that publishes it, and the
directory entry the rename creates is flushed too.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from shared.services.durable_fs import copy_file_durably, fsync_directory, fsync_parents, write_file_durably
from shared.services.testcase_files import write_testcase_files_into


class _FsyncSpy:
    """Record every descriptor handed to ``os.fsync``, and what it named."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[str] = []
        real = os.fsync

        def _record(fd: int) -> None:
            try:
                self.paths.append(os.readlink(f"/proc/self/fd/{fd}"))
            except OSError:  # pragma: no cover - non-Linux fallback
                self.paths.append("")
            real(fd)

        monkeypatch.setattr(os, "fsync", _record)


def test_a_durable_write_flushes_before_it_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rename must publish bytes that are already on the device."""
    spy = _FsyncSpy(monkeypatch)
    target = tmp_path / "001.in"

    write_file_durably(target, b"1 2\n")

    assert target.read_bytes() == b"1 2\n"
    assert any(path.endswith(".tmp") for path in spy.paths), "the temporary file was never flushed"


def test_a_failed_durable_write_leaves_no_temporary_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A full disk must not leave a half-written sibling next to the real file."""

    def _explode(*args: object, **kwargs: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "replace", _explode)
    target = tmp_path / "001.in"

    with pytest.raises(OSError, match="no space left"):
        write_file_durably(target, b"1 2\n")

    assert list(tmp_path.iterdir()) == []


def test_writing_a_test_case_flushes_its_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test-case write goes through the durable path, input and output alike."""
    spy = _FsyncSpy(monkeypatch)

    write_testcase_files_into(tmp_path, 1, b"in\n", b"out\n")

    flushed = [path for path in spy.paths if path.endswith(".tmp")]
    assert len(flushed) == 2, "both the input and the expected output must be flushed"


def test_fsync_directory_survives_a_filesystem_that_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not every filesystem can flush a directory handle; that must not fail a Save."""

    def _refuse(fd: int) -> None:
        raise OSError("directory fsync unsupported")

    monkeypatch.setattr(os, "fsync", _refuse)

    fsync_directory(tmp_path)  # must not raise


def test_fsync_parents_flushes_each_directory_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Promoting a whole test-case directory touches one parent, not one per case."""
    spy = _FsyncSpy(monkeypatch)
    (tmp_path / "problem").mkdir()

    fsync_parents([tmp_path / "problem" / name for name in ("001.in", "001.out", "002.in")])

    assert len([path for path in spy.paths if path.endswith("/problem")]) == 1


def test_a_durable_copy_flushes_what_it_wrote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hardlink fallback writes new bytes, so it owes the same flush a write does.

    A link publishes an inode that is already durable; a copy does not, and a
    directory flush alone would leave the entry present with its content missing.
    """
    spy = _FsyncSpy(monkeypatch)
    source = tmp_path / "001.in"
    source.write_bytes(b"1 2\n")
    target = tmp_path / "copy.in"

    copy_file_durably(source, target)

    assert target.read_bytes() == b"1 2\n"
    assert any(path.endswith("copy.in") for path in spy.paths)
