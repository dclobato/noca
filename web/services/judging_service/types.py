#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pydantic models and typed DTOs for judging flows."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from shared.enumerations import JudgmentStatus, Verdict


class VerdictOverrideRequest(BaseModel):
    """Request payload for verdict overrides."""

    new_verdict: Verdict
    reason: str = Field(..., min_length=10, max_length=1000)


class VerdictOverrideResponse(BaseModel):
    """Serialized verdict override response."""

    id: UUID
    submission_id: UUID
    original_verdict: Verdict
    new_verdict: Verdict
    reason: str
    overridden_by: UUID
    overridden_at: datetime


class JudgingHistoryEntry(BaseModel):
    """One entry in submission judging history."""

    judgment_id: UUID
    status: JudgmentStatus
    verdict: Verdict | None
    kind: Literal["auto", "rejudge", "override", "confirmation", "chief_confirmation"]
    actor_id: UUID | None
    actor_display: str | None
    timestamp: datetime
    timestamp_seconds: int | None
    reason: str | None


class JudgingHistoryResponse(BaseModel):
    """Full submission judging history response."""

    submission_id: UUID
    entries: list[JudgingHistoryEntry]


class ContestSetChiefJudgeRequest(BaseModel):
    """Request payload for chief-judge assignment."""

    judge_id: UUID
