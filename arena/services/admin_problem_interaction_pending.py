#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Sample-interaction edits deferred to the problem edit form's single Save.

Mirrors :mod:`arena.services.admin_problem_tc_pending`: the edit page marks
removals in a hidden ``si_remove_ids`` field and collects new rows as
``si_transcript_N`` / ``si_explanation_N`` groups, all riding the one form.

Parsing is split from applying so a malformed transcript is rejected before the
save mutates anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem
from arena.services import admin_problem_interaction_service
from shared.services.sample_interactions import MAX_SAMPLE_INTERACTIONS, parse_interaction_text

ParsedInteraction = tuple[dict[str, object], str | None]


def removal_ids(form_data: Mapping[str, Any]) -> set[str]:
    """Return the interaction ids the user marked for removal on the edit page."""
    raw = str(form_data.get("si_remove_ids", "") or "")
    return {value.strip() for value in raw.split(",") if value.strip()}


def parse_pending_interactions(form_data: Mapping[str, Any]) -> list[ParsedInteraction]:
    """Parse the inline add-rows without touching the database.

    Raises:
        InteractionParseError: If a row's transcript is malformed.
    """
    add_indices = sorted(
        {
            int(key.rsplit("_", 1)[1])
            for key in form_data
            if key.startswith("si_transcript_") and key.rsplit("_", 1)[1].isdigit()
        }
    )
    parsed: list[ParsedInteraction] = []
    for index in add_indices:
        raw = str(form_data.get(f"si_transcript_{index}", ""))
        # Blank rows are ones the author added and left empty; skip them. The parser
        # gets the *unstripped* text, because a protocol line's trailing spaces are
        # part of what the program wrote and must survive verbatim.
        if not raw.strip():
            continue
        explanation = str(form_data.get(f"si_explanation_{index}", "")).strip() or None
        parsed.append((parse_interaction_text(raw), explanation))
    return parsed


async def apply_pending_interactions(
    session: AsyncSession,
    problem: ArenaProblem,
    form_data: Mapping[str, Any],
    additions: list[ParsedInteraction],
) -> None:
    """Apply the removals and additions the edit form deferred to Save.

    Removals run **before** the additions, so an author who marks one of five
    interactions for removal can add its replacement in the same edit. The cap is
    therefore judged against the row count the save actually produces, not the one
    the problem started with, and it is checked up front so a save that cannot fit
    fails before it has mutated anything.

    Raises:
        ValueError: If the save's final row count would exceed the cap.
    """
    to_remove_ids = removal_ids(form_data)
    existing = await admin_problem_interaction_service.list_interactions(session, problem.id, include_hidden=True)
    to_remove = [item for item in existing if item.id in to_remove_ids]

    final_count = len(existing) - len(to_remove) + len(additions)
    if final_count > MAX_SAMPLE_INTERACTIONS:
        raise ValueError(
            f"A problem may have at most {MAX_SAMPLE_INTERACTIONS} sample interactions; "
            f"this save would leave {final_count}."
        )

    for interaction in sorted(to_remove, key=lambda item: item.ordinal, reverse=True):
        await admin_problem_interaction_service.delete_interaction(session, interaction)

    for transcript, explanation in additions:
        await admin_problem_interaction_service.create_interaction(
            session,
            problem,
            transcript=transcript,
            explanation=explanation,
        )
        await session.flush()
