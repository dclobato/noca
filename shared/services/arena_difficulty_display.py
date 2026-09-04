#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Evidence-gated presentation of an Arena problem's difficulty.

The rating worker stores a difficulty for *every* problem, including one nobody
has attempted: the Bayesian prior pins such a problem to the centre of the
scale on purpose. Displayed as a plain number, that centre value is
indistinguishable from a genuinely medium problem, so ``5.0`` would mean both
"medium" and "we have no idea". This module is the single place that decides
what a reader sees, keyed on the evidence behind the stored value.

The gate keys on the attempter count **alone**. An author's declared estimate
(see ``expected_difficulty``) is a prior, not evidence, so it never lets a
problem through the gate; it only fills the empty state with a clearly marked
estimate.

Pure module: no SQLAlchemy, no ORM. Callers pass the stored internal rating
(``[1, 100]``) and the attempter count and render the returned value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shared.enumerations import ArenaExpectedDifficulty

#: Minimum unique attempters before the stored difficulty is displayed as a
#: measurement. Below it the number is withheld, because at that point it is
#: dominated by the prior rather than by evidence.
MIN_ATTEMPTS_FOR_DISPLAY: int = 5

DifficultyState = Literal["measured", "estimated", "unknown"]

#: Text shown in place of a number when there is nothing to show.
UNKNOWN_TEXT = "—"


@dataclass(frozen=True, slots=True)
class DifficultyDisplay:
    """What one surface should show for a problem's difficulty.

    Attributes:
        value: Display-scale difficulty (0.1–10.0); ``None`` only when ``state``
            is ``"unknown"``.
        state: ``"measured"`` when the stored rating rests on enough attempts,
            ``"estimated"`` when it does not but the author declared an
            estimate, ``"unknown"`` otherwise.
        anchor_label: Worded anchor of an author estimate ("Challenging"), or
            ``None`` when the value is measured or not an exact anchor.
        attempted_users: Unique attempters the decision was made on.
    """

    value: float | None
    state: DifficultyState
    anchor_label: str | None
    attempted_users: int

    @property
    def text(self) -> str:
        """Short cell text: ``"7.0"``, ``"7.0?"``, or an em dash."""
        if self.value is None:
            return UNKNOWN_TEXT
        formatted = f"{self.value:.1f}"
        return f"{formatted}?" if self.state == "estimated" else formatted

    @property
    def bar_level(self) -> int | None:
        """Colour level in ``[1, 10]`` for the list progress bar, or ``None``."""
        if self.value is None:
            return None
        return max(1, min(10, round(self.value)))

    @property
    def description(self) -> str:
        """Full sentence for ``title`` and ``aria-label`` attributes."""
        if self.value is None:
            return "Not enough data yet"
        if self.state == "estimated":
            anchor = f" ({self.anchor_label})" if self.anchor_label else ""
            return f"Estimated difficulty {self.value:.1f} out of 10{anchor} — not enough submissions yet"
        return f"Difficulty {self.value:.1f} out of 10"


def difficulty_display(
    rating: int | None,
    attempted_users: int | None,
    expected_difficulty: int | None = None,
) -> DifficultyDisplay:
    """Decide how a stored difficulty should be presented.

    The gate and the label are separate decisions: the attempter count alone
    decides whether the stored rating is *measured*, and only then does the
    author estimate decide what fills the empty state.

    Args:
        rating: Stored internal difficulty in ``[1, 100]``, or ``None`` when the
            problem has no rating row yet.
        attempted_users: Unique attempters behind that rating; ``None`` counts as 0.
        expected_difficulty: Author's estimate on the internal scale, or ``None``.
            Never consulted once the threshold is met.

    Returns:
        DifficultyDisplay: The measured value when ``attempted_users`` reaches
        ``MIN_ATTEMPTS_FOR_DISPLAY``; below it, the author's estimate marked as
        such when one exists, otherwise the unknown state.
    """
    attempts = attempted_users or 0
    if rating is not None and attempts >= MIN_ATTEMPTS_FOR_DISPLAY:
        return DifficultyDisplay(value=rating / 10.0, state="measured", anchor_label=None, attempted_users=attempts)
    if expected_difficulty is not None:
        anchor = ArenaExpectedDifficulty.from_internal(expected_difficulty)
        return DifficultyDisplay(
            value=expected_difficulty / 10.0,
            state="estimated",
            anchor_label=anchor.label if anchor is not None else None,
            attempted_users=attempts,
        )
    return DifficultyDisplay(value=None, state="unknown", anchor_label=None, attempted_users=attempts)
