#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple, cast

from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.interfaces import ORMOption

from shared.enumerations import RoleEnum, Verdict
from shared.language_registry import highlightjs_language_for_language_id
from web.models import HumanSubmissionConfirmation, Submission, SubmissionJudgment, UberAdmin, User


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


@lru_cache(maxsize=32)
def submission_highlight_assets(language_id: str) -> dict[str, str]:
    """Return cached Highlight.js asset paths for one language."""
    highlight_language = highlightjs_language_for_language_id(language_id)
    return {
        "highlight_language_path": f"highlight/languages/{highlight_language}.min.js",
        "highlight_language_class": f"language-{highlight_language}",
    }


class ConfirmationSlot(NamedTuple):
    """Represents a single judge slot in the verdict confirmation panel."""

    verdict: Verdict | None
    judge_display: str | None
    is_mine: bool = False


class ConfirmationPanelData(NamedTuple):
    """All slot data required to render the 5-slot confirmation panel."""

    autojudge_verdict: Verdict | None
    judge_1: ConfirmationSlot
    judge_2: ConfirmationSlot
    chief_judge: ConfirmationSlot
    final_verdict: Verdict | None


def _build_confirmation_panel(
    judgment: SubmissionJudgment | None,
    actor: UberAdmin | User,
    has_confirmed: bool,
    is_chief_judge: bool = False,
) -> ConfirmationPanelData:
    """Build the 5-slot confirmation panel for the confirm page."""
    empty = ConfirmationSlot(verdict=None, judge_display=None)
    actor_id = getattr(actor, "id", None)

    if judgment is None:
        return ConfirmationPanelData(
            autojudge_verdict=None,
            judge_1=empty,
            judge_2=empty,
            chief_judge=empty,
            final_verdict=None,
        )

    is_admin_view = isinstance(actor, UberAdmin) or (
        isinstance(actor, User) and actor.role in {RoleEnum.ADMIN, RoleEnum.UBERADMIN}
    )
    others_visible = is_admin_view or is_chief_judge or has_confirmed

    confirmations = sorted(judgment.confirmations, key=lambda c: c.created_at)
    chief = next((c for c in confirmations if c.is_chief_confirmation), None)
    non_chief = [c for c in confirmations if not c.is_chief_confirmation]

    def _slot(conf: HumanSubmissionConfirmation | None, visible: bool) -> ConfirmationSlot:
        if conf is None or not visible:
            return empty
        judge_display: str | None = None
        if is_admin_view and conf.judge is not None:
            judge_display = conf.judge.fullname or conf.judge.username
        return ConfirmationSlot(
            verdict=conf.confirmed_verdict,
            judge_display=judge_display,
            is_mine=conf.judge_id == actor_id,
        )

    if chief is not None:
        return ConfirmationPanelData(
            autojudge_verdict=judgment.autojudge_verdict,
            judge_1=empty,
            judge_2=empty,
            chief_judge=_slot(chief, True),
            final_verdict=judgment.final_verdict,
        )

    j1 = non_chief[0] if len(non_chief) > 0 else None
    j2 = non_chief[1] if len(non_chief) > 1 else None
    return ConfirmationPanelData(
        autojudge_verdict=judgment.autojudge_verdict,
        judge_1=_slot(j1, others_visible),
        judge_2=_slot(j2, others_visible),
        chief_judge=empty,
        final_verdict=judgment.final_verdict,
    )


async def load_submission_in_contest(
    session: AsyncSession,
    submission_id: str,
    contest_id: str,
    *options: ORMOption,
) -> Submission | None:
    """Load a submission and ensure it belongs to the target contest."""
    result = await session.execute(select(Submission).where(Submission.id == submission_id).options(*options))
    submission = result.scalar_one_or_none()
    if submission is None or submission.problem.contest_id != contest_id:
        return None
    return submission
