#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Human-readable descriptions for fatal POSIX signal numbers.

Used by the Web and Arena UI layers to explain Runtime Error verdicts:
"killed by signal 11 (SIGSEGV — segmentation fault)" is actionable feedback,
a bare "RE" is not.
"""

import signal

_SIGNAL_EXPLANATIONS: dict[int, str] = {
    signal.SIGABRT: "aborted, e.g. failed assertion or C++ uncaught exception",
    signal.SIGBUS: "bus error, misaligned or invalid memory access",
    signal.SIGFPE: "arithmetic error, e.g. integer division by zero",
    signal.SIGILL: "illegal instruction",
    signal.SIGKILL: "killed by the system, e.g. out of memory",
    signal.SIGSEGV: "segmentation fault, invalid memory access",
    signal.SIGXCPU: "CPU time limit exceeded",
    signal.SIGXFSZ: "output file size limit exceeded",
    signal.SIGPIPE: "broken pipe",
    signal.SIGSYS: "bad system call",
}


def signal_name(signum: int) -> str:
    """Return the short signal name for a fatal signal number.

    Args:
        signum: POSIX signal number as reported by isolate's ``exitsig``.

    Returns:
        The signal name such as ``"SIGSEGV"``, or ``"SIG<n>"`` when unknown.
    """
    try:
        return signal.Signals(signum).name
    except ValueError:
        return f"SIG{signum}"


def describe_signal(signum: int) -> str:
    """Return a human-readable description for a fatal signal number.

    Args:
        signum: POSIX signal number as reported by isolate's ``exitsig``.

    Returns:
        A string such as ``"SIGSEGV — segmentation fault (invalid memory
        access)"``, or ``"signal <n>"`` when the number is unknown.
    """
    try:
        name = signal.Signals(signum).name
    except ValueError:
        return f"signal {signum}"

    explanation = _SIGNAL_EXPLANATIONS.get(signum)
    if explanation is None:
        return name
    return f"{name} — {explanation}"
