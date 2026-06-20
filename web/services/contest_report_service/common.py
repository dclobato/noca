#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers and constants for contest report aggregation."""

from __future__ import annotations

from shared.enumerations import Verdict

ALL_VERDICTS: list[Verdict] = [
    Verdict.AC,
    Verdict.PE,
    Verdict.WA,
    Verdict.TLE,
    Verdict.MLE,
    Verdict.OLE,
    Verdict.RE,
    Verdict.CE,
]


def ordinal_to_label(ordinal: int) -> str:
    """Convert a 1-based problem ordinal to a label."""
    ordinal -= 1
    label = ""
    while True:
        label = chr(ord("A") + ordinal % 26) + label
        ordinal = ordinal // 26 - 1
        if ordinal < 0:
            break
    return label


def pct(count: int, total: int) -> float:
    """Return percentage rounded to two decimals."""
    return round(count / total * 100, 2) if total else 0.0
