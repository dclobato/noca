#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The sample-interaction rows typed inline on a judgment page.

Existing interactions apply immediately. These rows are the only interaction
state still held by the browser, so parsing retains their indices and raw text:
a rejected row can be rendered again beside the exact error instead of being
discarded by a redirect.

The module is ORM-free: both adapters own persistence, while this layer owns the
form contract and precise validation attribution only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shared.services.sample_interactions import InteractionParseError, parse_interaction_text


@dataclass(frozen=True, slots=True)
class SubmittedInteractionRow:
    """One submitted interaction add-row, including its validation state."""

    index: int
    transcript_text: str
    explanation: str
    error: str = ""

    @property
    def has_content(self) -> bool:
        """Return whether the row contains a transcript to save."""
        return bool(self.transcript_text.strip())


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """One validated interaction paired with its submitted row index."""

    index: int
    transcript: dict[str, object]
    explanation: str | None


class SubmittedInteractionError(InteractionParseError):
    """A malformed submitted interaction, retaining the row that caused it."""

    def __init__(self, index: int, message: str) -> None:
        """Initialize an error for ``index`` with an actionable message."""
        super().__init__(message)
        self.index = index


def submitted_interaction_rows(form: Mapping[str, Any]) -> tuple[SubmittedInteractionRow, ...]:
    """Return every submitted interaction row, including blank drafts."""
    indices = sorted(
        {
            int(key.rsplit("_", 1)[1])
            for key in form
            if key.startswith(("si_transcript_", "si_explanation_")) and key.rsplit("_", 1)[1].isdigit()
        }
    )
    return tuple(
        SubmittedInteractionRow(
            index=index,
            transcript_text=str(form.get(f"si_transcript_{index}", "")),
            explanation=str(form.get(f"si_explanation_{index}", "")),
        )
        for index in indices
    )


def parse_pending_interactions(form: Mapping[str, Any]) -> list[PendingInteraction]:
    """Parse nonblank rows and identify a malformed row precisely.

    Raises:
        SubmittedInteractionError: If a transcript is malformed. Its ``index``
            identifies the row the response must retain and focus.
    """
    parsed: list[PendingInteraction] = []
    for row in submitted_interaction_rows(form):
        if not row.has_content:
            continue
        try:
            transcript = parse_interaction_text(row.transcript_text)
        except InteractionParseError as exc:
            raise SubmittedInteractionError(row.index, str(exc)) from exc
        parsed.append(
            PendingInteraction(
                index=row.index,
                transcript=transcript,
                explanation=row.explanation.strip() or None,
            )
        )
    return parsed
