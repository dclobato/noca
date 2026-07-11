#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Shared runtime helper predicates used across autojudge job pipelines.
"""


def decode_for_text_column(data: bytes) -> str:
    """Decode untrusted program/compiler output for a PostgreSQL text column.

    ``errors="replace"`` only substitutes byte sequences that are invalid UTF-8,
    but a NUL byte (``0x00``) is *valid* UTF-8 (it decodes to U+0000) yet cannot
    be stored in a PostgreSQL ``text``/``varchar`` column. Left in place it makes
    the INSERT fail with ``invalid byte sequence for encoding "UTF8": 0x00``,
    which aborts the transaction. Strip NUL characters after decoding so captured
    output is always safe to persist.

    Args:
        data: Raw bytes captured from a sandboxed program or the compiler.

    Returns:
        A str safe to store in a text column, with NUL characters removed.
    """
    return data.decode(errors="replace").replace("\x00", "")


def is_recoverable_isolate_runtime_error(exc: Exception) -> bool:
    """
    Return whether an isolate failure looks like transient container breakage.

    Two known-recoverable signatures are matched: the missing-cgroup-file
    pattern, and the suspicious instant signal kill raised by the runner when a
    process dies at exec time with no output (sandbox breakage misreported by
    isolate as a contestant signal).

    Args:
        exc: Runtime exception raised while executing or profiling a test case.

    Returns:
        ``True`` when the error matches a known recoverable pattern, otherwise
        ``False``.
    """
    message = str(exc)
    if "suspicious sandbox signal kill" in message:
        return True
    return "/sys/fs/cgroup/box-" in message and "No such file or directory" in message
