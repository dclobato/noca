#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Bounded side-by-side comparison of a student's output with the expected output.

The submission detail page used to render the failing case's whole expected
output, sample or secret. On an educational platform a bare "wrong on a secret
case" teaches nothing, but the whole answer teaches too much: repeated wrong
submissions would walk the secret set one case at a time. This module produces
the middle ground -- the first few *differing* lines, side by side, with one
line of context -- from two inputs that are both bounded before they get here:

- the student's output is the excerpt the judge persisted, at most
  ``NOCA_JUDGE_STDOUT_EXCERPT_BYTES`` (default 8 KB, :data:`DEFAULT_EXCERPT_BYTES`);
- the expected output is a fixed-size prefix read by
  :func:`shared.services.testcase_files.read_testcase_output_prefix`.

Because the page can never compare beyond the stored excerpt, the excerpt cap
also bounds how much of a secret case's expected output any sequence of wrong
submissions can reveal. That trade-off is accepted and stated in
``docs/ARCHITECTURE.md``.

The module is pure: no I/O, no ORM, no settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import zip_longest
from typing import Literal

from shared.enumerations import Verdict

OUTPUT_MISMATCH_VERDICTS = frozenset({Verdict.WA, Verdict.PE})
"""Verdicts whose failing case has an expected output worth comparing against.

A time-limit or runtime failure has no answer to contrast, so the pages skip the
expected-output read entirely for every other verdict.
"""

DEFAULT_EXCERPT_BYTES = 8192
"""The judge's default ``NOCA_JUDGE_STDOUT_EXCERPT_BYTES``.

Arena has no access to the judge's setting, so a stored excerpt at or beyond this
size is treated as cut. A deployment with a larger cap simply loses the
"cut" hint and shows its final partial line as an ordinary difference.
"""

EXPECTED_PREFIX_BYTES = 16384
"""How much of the expected output the detail pages read.

Twice the judge's default excerpt, so the expected side always covers the whole
comparable window of the student's output; anything past it can never appear on
a page because there is no student output to compare it with.
"""

MAX_DIFF_ROWS = 6
"""How many differing line pairs a comparison shows before folding the rest."""

CONTEXT_LINES = 1
"""How many unchanged lines are kept around each shown difference."""

MAX_LINE_CHARS = 300
"""Rendered lines longer than this are cut and marked, so one line cannot flood the page."""

RowKind = Literal["equal", "changed", "whitespace", "missing", "extra"]


@dataclass(frozen=True)
class DiffRow:
    """One rendered line pair of the side-by-side comparison.

    Attributes:
        kind: ``equal`` (context), ``changed`` (both sides present and different
            in content), ``whitespace`` (same tokens, different spacing -- the
            judge's PE notion), ``missing`` (the expected output has a line the
            student's output lacks), or ``extra`` (the student printed a line the
            expected output does not have).
        left_no: 1-based line number in the student's output, or None.
        left: The student's line, or None when absent.
        right_no: 1-based line number in the expected output, or None.
        right: The expected line, or None when absent.
    """

    kind: RowKind
    left_no: int | None
    left: str | None
    right_no: int | None
    right: str | None


@dataclass(frozen=True)
class OutputComparison:
    """View-model for the output comparison block of a failed test case.

    Attributes:
        rows: The rows to render, in order: at most :data:`MAX_DIFF_ROWS`
            differing pairs plus their context lines.
        differing_lines: Total differing pairs found inside the compared window.
        hidden_differing_lines: Differing pairs beyond the ones shown.
        actual_cut: The stored student excerpt hit the judge's cap, so its final
            partial line was dropped and nothing past it is known.
        expected_cut: The expected output is longer than the prefix read, so the
            comparison stops where the prefix does.
        difference_beyond_excerpt: No difference exists inside the compared
            window although the verdict says one exists, and at least one side
            was cut -- the difference lies past what was stored or read.
        no_visible_difference: No line-level difference exists and neither side
            was cut; the judge's comparison differed on something line splitting
            hides (typically trailing whitespace or a final newline).
    """

    rows: tuple[DiffRow, ...]
    differing_lines: int
    hidden_differing_lines: int
    actual_cut: bool
    expected_cut: bool
    difference_beyond_excerpt: bool
    no_visible_difference: bool


def _clip(line: str) -> str:
    if len(line) <= MAX_LINE_CHARS:
        return line
    return line[:MAX_LINE_CHARS] + " […]"


def _pair_kind(left: str, right: str) -> RowKind:
    if left == right:
        return "equal"
    if left.split() == right.split():
        return "whitespace"
    return "changed"


def _expand_opcodes(actual: list[str], expected: list[str]) -> list[DiffRow]:
    """Turn the matcher's opcodes into one row per line pair, context included."""
    rows: list[DiffRow] = []
    matcher = SequenceMatcher(None, actual, expected, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                line = actual[i1 + offset]
                rows.append(DiffRow("equal", i1 + offset + 1, _clip(line), j1 + offset + 1, _clip(line)))
            continue
        left_side = [(i + 1, actual[i]) for i in range(i1, i2)]
        right_side = [(j + 1, expected[j]) for j in range(j1, j2)]
        for left, right in zip_longest(left_side, right_side):
            if left is None:
                rows.append(DiffRow("missing", None, None, right[0], _clip(right[1])))
            elif right is None:
                rows.append(DiffRow("extra", left[0], _clip(left[1]), None, None))
            else:
                kind = _pair_kind(left[1], right[1])
                rows.append(DiffRow(kind, left[0], _clip(left[1]), right[0], _clip(right[1])))
    return rows


def _select_rows(rows: list[DiffRow]) -> tuple[list[DiffRow], int, int]:
    """Keep the first differing rows and their context; report totals."""
    differing = [index for index, row in enumerate(rows) if row.kind != "equal"]
    shown = differing[:MAX_DIFF_ROWS]
    keep: set[int] = set(shown)
    for index in shown:
        for neighbour in range(index - CONTEXT_LINES, index + CONTEXT_LINES + 1):
            # Context is unchanged lines only; a folded difference must stay folded.
            if 0 <= neighbour < len(rows) and rows[neighbour].kind == "equal":
                keep.add(neighbour)
    selected = [rows[index] for index in sorted(keep)]
    return selected, len(differing), len(differing) - len(shown)


def build_output_comparison(
    actual_excerpt: str | None,
    expected_prefix: str,
    *,
    expected_cut: bool,
) -> OutputComparison:
    """Compare the stored student excerpt with a prefix of the expected output.

    Args:
        actual_excerpt: The judge-persisted ``stdout_excerpt``, possibly cut at
            the judge's byte cap, or None when the student printed nothing.
        expected_prefix: The first bytes of the expected output, decoded.
        expected_cut: Whether ``expected_prefix`` is shorter than the file.

    Returns:
        The bounded side-by-side comparison to render.
    """
    actual = actual_excerpt or ""
    actual_lines = actual.splitlines()
    expected_lines = expected_prefix.splitlines()

    actual_cut = bool(actual) and not actual.endswith(("\n", "\r")) and len(actual.encode()) >= DEFAULT_EXCERPT_BYTES
    if actual_cut and actual_lines:
        actual_lines.pop()
    if expected_cut and expected_lines and not expected_prefix.endswith("\n"):
        expected_lines.pop()

    # Nothing is known past a cut side, so the other side is compared only as far.
    if actual_cut:
        expected_lines = expected_lines[: len(actual_lines)]
    if expected_cut:
        actual_lines = actual_lines[: len(expected_lines)]

    rows, differing, hidden = _select_rows(_expand_opcodes(actual_lines, expected_lines))
    any_cut = actual_cut or expected_cut
    return OutputComparison(
        rows=tuple(rows),
        differing_lines=differing,
        hidden_differing_lines=hidden,
        actual_cut=actual_cut,
        expected_cut=expected_cut,
        difference_beyond_excerpt=differing == 0 and any_cut,
        no_visible_difference=differing == 0 and not any_cut,
    )
