#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Serialization helpers for scoreboard snapshots."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import ProblemResult, ScoreboardSnapshot, TeamStanding


def snapshot_to_dict(snapshot: ScoreboardSnapshot) -> dict[str, Any]:
    """Serialize a scoreboard snapshot to a JSON-compatible dict."""
    return asdict(snapshot)


def snapshot_from_dict(data: dict[str, Any]) -> ScoreboardSnapshot:
    """Deserialize a scoreboard snapshot from a JSON-compatible dict."""
    standings = [
        TeamStanding(
            rank=row["rank"],
            team_id=row["team_id"],
            team_name=row["team_name"],
            team_fullname=row.get("team_fullname", row["team_name"]),
            problems_solved=row["problems_solved"],
            total_time=row["total_time"],
            problems={
                label: ProblemResult(**{**problem, "is_first_balloon": problem.get("is_first_balloon", False)})
                for label, problem in row["problems"].items()
            },
        )
        for row in data["standings"]
    ]
    return ScoreboardSnapshot(
        contest_id=data["contest_id"],
        generated_at=data["generated_at"],
        is_frozen=data["is_frozen"],
        standings=standings,
        problems=data["problems"],
        balloon_colors=data["balloon_colors"],
    )
