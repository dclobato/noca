#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Scoreboard service orchestration and caching."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from shared.enumerations import JudgmentStatus, RoleEnum
from shared.services.scoreboard_cache import (
    invalidate_scoreboard_cache,
    scoreboard_final_key,
    scoreboard_frozen_key,
    scoreboard_full_key,
    scoreboard_public_key,
)
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User

from .computation import compute_icpc, ordinal_to_label
from .models import ScoreboardSnapshot, TeamStanding
from .serialization import snapshot_from_dict, snapshot_to_dict

logger = logging.getLogger(__name__)

_TTL_PUBLIC_S = 180
_TTL_FULL_S = 5


class ScoreboardService:
    """Computes and caches the ICPC scoreboard for a contest."""

    async def compute_standings(
        self,
        contest: Contest,
        viewer_role: str,
        db: AsyncSession,
    ) -> ScoreboardSnapshot:
        """Compute a fresh scoreboard snapshot from the database."""
        viewer_sees_frozen = viewer_role not in ("admin", "judge")
        teams = await self._load_teams(db, contest)
        problems = await self._load_problems(db, contest)
        submissions, judgments = await self._load_submission_rows(db, contest)
        standings = self._compute_icpc(
            contest=contest,
            teams=teams,
            problems=problems,
            submissions=submissions,
            judgments=judgments,
            freeze_at_seconds=contest.stop_updating_scoreboard * 60,
            viewer_sees_frozen=viewer_sees_frozen,
        )
        labels = [ordinal_to_label(problem.ordinal) for problem in problems]
        balloon_colors = [problem.color.lstrip("#") for problem in problems]
        return ScoreboardSnapshot(
            contest_id=str(contest.id),
            generated_at=datetime.utcnow().isoformat() + "Z",
            is_frozen=contest.is_scoreboard_frozen,
            standings=standings,
            problems=labels,
            balloon_colors=balloon_colors,
        )

    async def get_cached_or_compute(
        self,
        contest: Contest,
        viewer_role: str,
        db: AsyncSession,
        valkey: Any,
    ) -> ScoreboardSnapshot:
        """Return a cached scoreboard snapshot or compute a fresh one."""
        is_full = viewer_role in ("admin", "judge")

        if not is_full and contest.is_scoreboard_frozen:
            frozen_key = scoreboard_frozen_key(str(contest.id))
            try:
                cached = await valkey.get(frozen_key)
                if cached is not None:
                    return snapshot_from_dict(json.loads(cached))
            except Exception as exc:
                logger.warning("Frozen scoreboard cache read failed for %s: %s", frozen_key, exc)

            snapshot = await self.compute_standings(contest, viewer_role, db)
            try:
                await valkey.set(frozen_key, json.dumps(snapshot_to_dict(snapshot)))
            except Exception as exc:
                logger.warning("Frozen scoreboard cache write failed for %s: %s", frozen_key, exc)
            return snapshot

        cache_key = scoreboard_full_key(str(contest.id)) if is_full else scoreboard_public_key(str(contest.id))
        ttl = _TTL_FULL_S if is_full else _TTL_PUBLIC_S

        try:
            cached = await valkey.get(cache_key)
            if cached is not None:
                return snapshot_from_dict(json.loads(cached))
        except Exception as exc:
            logger.warning("Scoreboard cache read failed for %s: %s", cache_key, exc)

        snapshot = await self.compute_standings(contest, viewer_role, db)
        try:
            await valkey.set(cache_key, json.dumps(snapshot_to_dict(snapshot)), ex=ttl)
        except Exception as exc:
            logger.warning("Scoreboard cache write failed for %s: %s", cache_key, exc)
        return snapshot

    async def get_or_compute_final(
        self,
        contest: Contest,
        db: AsyncSession,
        valkey: Any,
    ) -> ScoreboardSnapshot:
        """Return the permanent final scoreboard or compute and store it."""
        cache_key = scoreboard_final_key(str(contest.id))

        try:
            cached = await valkey.get(cache_key)
            if cached is not None:
                return snapshot_from_dict(json.loads(cached))
        except Exception as exc:
            logger.warning("Final scoreboard cache read failed for %s: %s", cache_key, exc)

        snapshot = await self.compute_standings(contest, "admin", db)
        snapshot.is_frozen = False

        try:
            await valkey.set(cache_key, json.dumps(snapshot_to_dict(snapshot)))
        except Exception as exc:
            logger.warning("Final scoreboard cache write failed for %s: %s", cache_key, exc)

        try:
            await valkey.delete(scoreboard_frozen_key(str(contest.id)))
        except Exception as exc:
            logger.warning("Frozen scoreboard cache deletion failed for %s: %s", contest.id, exc)
        return snapshot

    async def invalidate_cache(self, contest_id: str, valkey: Any) -> None:
        """Invalidate the short-lived scoreboard cache keys for a contest."""
        await invalidate_scoreboard_cache(valkey, contest_id)

    def _compute_icpc(
        self,
        contest: Contest,
        teams: list[User],
        problems: list[Problem],
        submissions: list[Submission],
        judgments: dict[str, SubmissionJudgment | None],
        freeze_at_seconds: int,
        viewer_sees_frozen: bool,
    ) -> list[TeamStanding]:
        """Delegate to the pure scoring implementation."""
        return compute_icpc(
            contest=contest,
            teams=teams,
            problems=problems,
            submissions=submissions,
            judgments=judgments,
            freeze_at_seconds=freeze_at_seconds,
            viewer_sees_frozen=viewer_sees_frozen,
        )

    async def _load_teams(self, db: AsyncSession, contest: Contest) -> list[User]:
        result = await db.execute(
            select(User).where(User.contest_id == contest.id, User.role == RoleEnum.TEAM).order_by(User.username)
        )
        return list(result.scalars().all())

    async def _load_problems(self, db: AsyncSession, contest: Contest) -> list[Problem]:
        result = await db.execute(select(Problem).where(Problem.contest_id == contest.id).order_by(Problem.ordinal))
        return list(result.scalars().all())

    async def _load_submission_rows(
        self,
        db: AsyncSession,
        contest: Contest,
    ) -> tuple[list[Submission], dict[str, SubmissionJudgment | None]]:
        rows = (
            await db.execute(
                select(Submission, SubmissionJudgment)
                .join(Problem, Submission.problem_id == Problem.id)
                .outerjoin(
                    SubmissionJudgment,
                    and_(
                        SubmissionJudgment.submission_id == Submission.id,
                        SubmissionJudgment.status != JudgmentStatus.SUPERSEDED,
                    ),
                )
                .where(Problem.contest_id == contest.id)
                .order_by(Submission.team_id, Submission.problem_id, Submission.timestamp_seconds)
            )
        ).all()

        judgments: dict[str, SubmissionJudgment | None] = {}
        submissions: list[Submission] = []
        seen_submission_ids: set[str] = set()
        for submission, judgment in rows:
            if submission.id not in seen_submission_ids:
                submissions.append(submission)
                seen_submission_ids.add(submission.id)
            existing = judgments.get(submission.id)
            if existing is None or judgment is not None and judgment.status == JudgmentStatus.DONE:
                judgments[submission.id] = judgment

        return submissions, judgments
