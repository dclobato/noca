#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Output comparison and verdict aggregation logic.

This module is intentionally pure — no I/O, no Docker calls, no database
access. Every function takes plain Python values and returns plain Python
values, making them straightforward to unit-test exhaustively.

Comparison strategy
-------------------
The compare_output() function implements the three-level comparison that
covers the vast majority of competitive programming problems:

    AC  — output matches expected exactly (byte-for-byte after normalisation)
    PE  — tokens match but whitespace/newline formatting differs
    WA  — tokens do not match

"Token" here means a maximal sequence of non-whitespace characters, which is
the standard definition used by ICPC judges and DOMjudge.

Aggregation
-----------
aggregate_verdict() applies VERDICT_PRIORITY to a list of per-test-case
results and returns the single verdict that represents the submission.
With fail-fast execution there will typically be at most one non-AC result,
but the function is correct for the general case as well.
"""

from dataclasses import dataclass, field

from shared.enumerations import VERDICT_PRIORITY, Verdict

# ---------------------------------------------------------------------------
# Per-test-case result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    """
    Result of running a submission against a single test case.

    Produced by judge/runner.py and consumed by:
    - aggregate_verdict()  to compute the final submission verdict
    - the worker           to persist a submission_test_result row
    """

    verdict: Verdict

    # Resource usage (None if the process was killed before reporting)
    wall_time_ms: int | None = None
    memory_kb: int | None = None
    output_bytes: int | None = None
    peak_pids: int | None = None
    exit_code: int | None = None
    exit_signal: int | None = None

    # Truncated output for UI display (never the full stdout)
    stdout_excerpt: bytes = field(default_factory=bytes)
    stderr_excerpt: bytes = field(default_factory=bytes)

    # Message from a special judge checker, if applicable
    checker_output: str | None = None


# ---------------------------------------------------------------------------
# Output comparison
# ---------------------------------------------------------------------------


def _tokenise(data: bytes) -> list[bytes]:
    """
    Split byte string into whitespace-delimited tokens.
    Equivalent to str.split() but operates on raw bytes.
    """
    return data.split()


def _normalise_lines(data: bytes) -> bytes:
    """
    Strip trailing whitespace from every line and remove trailing blank lines.
    Used for the PE check: two outputs are PE if their normalised forms match.
    """
    lines = data.splitlines()
    stripped = [line.rstrip() for line in lines]
    # Remove trailing empty lines
    while stripped and not stripped[-1]:
        stripped.pop()
    return b"\n".join(stripped)


def compare_output(
    actual: bytes,
    expected: bytes,
    *,
    ignore_trailing_whitespace: bool = True,
) -> Verdict:
    """
    Compare contestant output against expected output and return a verdict.

    Parameters
    ----------
    actual:
        Raw bytes read from the contestant's stdout (already capped at
        OUTPUT_LIMIT by the runner — do not pass unbounded data here).
    expected:
        Full expected output read from the test case file.
    ignore_trailing_whitespace:
        When True (default), trailing whitespace on each line and trailing
        blank lines are ignored for the AC check, which is the ICPC standard.
        Set to False for problems that require exact byte-for-byte output.

    Returns
    -------
    Verdict.AC   — output is correct (within whitespace tolerance)
    Verdict.PE   — tokens match but whitespace structure differs
    Verdict.WA   — tokens do not match
    """

    # Fast path: exact match
    if actual == expected:
        return Verdict.AC

    # In strict mode (ignore_trailing_whitespace=False) there is no PE —
    # anything that is not an exact byte match is WA.
    if not ignore_trailing_whitespace:
        return Verdict.WA

    # Whitespace-tolerant AC: normalise trailing spaces and blank lines
    if _normalise_lines(actual) == _normalise_lines(expected):
        return Verdict.AC

    # Token-level comparison: same tokens, different whitespace structure → PE
    if _tokenise(actual) == _tokenise(expected):
        return Verdict.PE

    return Verdict.WA


# ---------------------------------------------------------------------------
# Verdict aggregation
# ---------------------------------------------------------------------------


def aggregate_verdict(results: list[CaseResult]) -> Verdict:
    """
    Reduce a list of per-test-case results to a single final verdict.

    With fail-fast execution there will typically be exactly one non-AC result
    at most, but this function is correct for any combination of results.

    The reduction applies VERDICT_PRIORITY: the first (most severe) verdict
    found in the priority list wins. AC is returned only if every test case
    passed.

    Raises
    ------
    ValueError
        If results is empty (no test cases were run — this is a judge bug,
        not a contestant error).
    """
    if not results:
        raise ValueError(
            "aggregate_verdict() called with an empty results list. At least one test case must have been executed."
        )

    result_verdicts = {r.verdict for r in results}

    for verdict in VERDICT_PRIORITY:
        if verdict in result_verdicts:
            return verdict

    # Should be unreachable: every Verdict value is in VERDICT_PRIORITY
    return Verdict.AC  # pragma: no cover


def worst_resource_usage(results: list[CaseResult]) -> dict[str, int | None]:
    """
    Return the peak resource usage across all test case results.
    Useful for reporting the maximum wall time and memory seen during judging.
    """
    wall_times = [r.wall_time_ms for r in results if r.wall_time_ms is not None]
    memories = [r.memory_kb for r in results if r.memory_kb is not None]
    output_sizes = [r.output_bytes for r in results if r.output_bytes is not None]

    return {
        "peak_wall_time_ms": max(wall_times) if wall_times else None,
        "peak_memory_kb": max(memories) if memories else None,
        "peak_output_bytes": max(output_sizes) if output_sizes else None,
        "min_wall_time_ms": min(wall_times) if wall_times else None,
        "min_memory_kb": min(memories) if memories else None,
    }
