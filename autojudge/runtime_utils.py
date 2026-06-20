#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Shared runtime helper predicates used across autojudge job pipelines.
"""


def is_recoverable_isolate_runtime_error(exc: Exception) -> bool:
    """
    Return whether an isolate failure looks like transient container cgroup breakage.

    Args:
        exc: Runtime exception raised while executing or profiling a test case.

    Returns:
        ``True`` when the error matches the known recoverable missing-cgroup-file
        pattern, otherwise ``False``.
    """
    message = str(exc)
    return "/sys/fs/cgroup/box-" in message and "No such file or directory" in message
