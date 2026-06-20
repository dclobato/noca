#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena help pages: rating system and languages/verdicts documentation."""

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from shared.db_schema import languages as languages_table
from shared.enumerations import Verdict
from shared.services.arena_rating import (
    ALPHA,
    BASE_POINTS,
    BETA,
    CONFIDENCE_SCALE,
    CONTRAST_GAIN_MAX,
    CONTRAST_GAIN_SCALE,
    GROWTH,
    MAX_RELEVANT_TRIES,
    PRIOR_SOLVE_RATE,
    PRIOR_TRIES,
    W_SOLVE_RATE,
    W_TRIES,
    _points_for_difficulty,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["arena-help"])

_VERDICT_DESCRIPTIONS: dict[Verdict, str] = {
    Verdict.AC: (
        "Accepted — the submission produced the correct output for all test cases within the resource limits."
    ),
    Verdict.WA: "Wrong Answer — the output did not match the expected answer for at least one test case.",
    Verdict.CE: "Compilation Error — the source code could not be compiled or parsed by the language toolchain.",
    Verdict.RE: (
        "Runtime Error — the program terminated abnormally (non-zero exit code, crash, or signal)"
        " during at least one test case."
    ),
    Verdict.TLE: (
        "Time Limit Exceeded — the program did not finish within the allowed time for at least one test case."
    ),
    Verdict.MLE: (
        "Memory Limit Exceeded — the program exceeded the memory limit and was terminated by the OS (OOM kill)."
    ),
    Verdict.OLE: "Output Limit Exceeded — the program wrote more output than allowed and was terminated.",
    Verdict.PE: (
        "Presentation Error — the output is correct but formatted incorrectly (e.g. extra spaces, wrong line endings)."
    ),
}


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get("/help/rating", response_class=HTMLResponse, name="arena_help_rating")
async def arena_help_rating(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> HTMLResponse:
    """Render the Arena rating system help page.

    Displays documentation on how problem difficulty, user score, and
    affiliation ratings are computed, along with the configured rating
    update interval.

    Args:
        request: The current HTTP request.
        current_user: Authenticated ``ArenaUser`` or ``None`` for guests.
    """
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "help_rating.html",
            {
                "current_user": current_user,
                "rating_interval_text": getattr(request.app.state, "rating_interval_text", None),
                "affiliation_factor": getattr(request.app.state, "affiliation_rating_factor", None),
                "alpha": ALPHA,
                "beta": BETA,
                "prior_solve_rate": PRIOR_SOLVE_RATE,
                "prior_tries": PRIOR_TRIES,
                "max_relevant_tries": MAX_RELEVANT_TRIES,
                "w_solve_rate": W_SOLVE_RATE,
                "w_tries": W_TRIES,
                "base_points": BASE_POINTS,
                "growth": GROWTH,
                "pts_difficulty_1": round(_points_for_difficulty(1)),
                "pts_difficulty_10": round(_points_for_difficulty(100)),
                "pts_ratio": round(_points_for_difficulty(100) / _points_for_difficulty(1)),
                "confidence_scale": CONFIDENCE_SCALE,
                "contrast_gain_max": CONTRAST_GAIN_MAX,
                "contrast_gain_scale": CONTRAST_GAIN_SCALE,
            },
        )
    )


@router.get("/help/languages", response_class=HTMLResponse, name="arena_help_languages")
async def arena_help_languages(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the Arena languages and verdicts help page.

    Lists all active languages with their versions and compile/run commands,
    and explains the meaning of each possible judgment verdict.

    Args:
        request: The current HTTP request.
        current_user: Authenticated ``ArenaUser`` or ``None`` for guests.
        session: Database session for language registry queries.
    """
    result = await session.execute(
        select(languages_table).where(languages_table.c.active == True).order_by(languages_table.c.name)  # noqa: E712
    )
    active_languages = result.mappings().all()

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "help_languages.html",
            {
                "current_user": current_user,
                "languages": active_languages,
                "verdicts": _VERDICT_DESCRIPTIONS,
            },
        )
    )
