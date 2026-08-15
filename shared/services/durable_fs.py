#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Making a write survive the crash the journal is written to recover from.

The editor's crash-safety argument is a comparison between two systems: after a
host crash, PostgreSQL holds a problem's ``artifact_generation`` and recovery
decides from it whether the matching files should be kept or put back. That
argument only holds if the files are as durable as the row. A ``write_bytes``
followed by a ``rename`` is neither -- the kernel may still hold both the data
and the directory entry in its page cache -- so a crash can leave a committed
generation pointing at content that never reached the disk, which is precisely
the state recovery has no way to detect.

Two operations close that gap, and both are needed: the *data* must be flushed
before the rename that publishes it, and the *directory entry* the rename creates
must be flushed too, or the file may exist with nothing naming it.

Directory flushing is best-effort by design. Not every filesystem supports
``fsync`` on a directory handle, and failing a save because the platform cannot
offer the strongest guarantee would be worse than the guarantee is worth; the
call sites are all inside the swap, which recovers from an unflushed rename the
same way it recovers from an interrupted one.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def write_file_durably(target: Path, content: bytes) -> None:
    """Write ``content`` to ``target`` without writing through its inode.

    ``target`` may be a hardlink to a live file -- that is how editor staging is
    seeded -- so it is never opened for writing. The bytes go to a temporary
    sibling that is flushed to the device before the rename publishes it, which
    replaces the directory entry and leaves every other link untouched.

    Args:
        target: Where the content belongs.
        content: The bytes to write.
    """
    temporary = target.with_name(f".noca-write-{target.name}.tmp")
    try:
        with open(temporary, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def copy_file_durably(source: Path, target: Path) -> None:
    """Copy ``source`` to a *new* ``target``, flushed to the device.

    Streamed rather than read into memory: this is the hardlink fallback, and a
    single test case may be gigabytes. No temporary name is needed because the
    target is a path inside a freshly created staging directory that no other
    reader knows about; a crash partway through abandons the whole directory.

    Args:
        source: The file to copy.
        target: Where the copy goes. Must not already be read by anyone.
    """
    with open(source, "rb") as reader, open(target, "wb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())


def fsync_directory(directory: Path) -> None:
    """Flush a directory entry so a rename into it survives a crash.

    Best-effort: a filesystem that cannot flush a directory handle simply keeps
    the weaker guarantee it already had.

    Args:
        directory: The directory whose entries were just changed.
    """
    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def fsync_parents(paths: list[Path]) -> None:
    """Flush each distinct parent directory of ``paths``, once.

    Renaming a whole test-case directory touches one parent however many files it
    holds, so the flush is per directory rather than per artifact.

    Args:
        paths: Paths whose directory entries were just created or removed.
    """
    seen: set[Path] = set()
    for path in paths:
        parent = path.parent
        if parent in seen:
            continue
        seen.add(parent)
        fsync_directory(parent)
