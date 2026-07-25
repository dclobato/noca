#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Collect and render the JSON metadata members of a contest backup.

This module owns the read-side of the export: it queries the persisted replay
dataset for one contest and serializes it into the deterministic JSON members
that :mod:`web.services.contest_backup_service.export` writes into the archive.
The bulky per-problem package bytes are handled by the caller, not here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import (
    clarifications as clarifications_t,
)
from shared.db_schema import (
    contest_languages as contest_languages_t,
)
from shared.db_schema import (
    contests as contests_t,
)
from shared.db_schema import (
    human_submission_confirmations as confirmations_t,
)
from shared.db_schema import (
    problem_categories as problem_categories_t,
)
from shared.db_schema import (
    problem_categories_map as problem_categories_map_t,
)
from shared.db_schema import (
    problem_custom_validators as validators_t,
)
from shared.db_schema import (
    problem_language_limits as language_limits_t,
)
from shared.db_schema import (
    problem_sample_interactions as sample_interactions_t,
)
from shared.db_schema import (
    problems as problems_t,
)
from shared.db_schema import (
    sites as sites_t,
)
from shared.db_schema import (
    submission_interactive_attempts as interactive_attempts_t,
)
from shared.db_schema import (
    submission_judgment_audit as judgment_audit_t,
)
from shared.db_schema import (
    submission_judgments as judgments_t,
)
from shared.db_schema import (
    submission_test_results as test_results_t,
)
from shared.db_schema import (
    submissions as submissions_t,
)
from shared.db_schema import (
    tasks as tasks_t,
)
from shared.db_schema import (
    test_cases as test_cases_t,
)
from shared.db_schema import (
    users as users_t,
)
from shared.db_schema import (
    users_media as users_media_t,
)
from shared.db_schema import (
    verdict_overrides as verdict_overrides_t,
)
from web.models.contest import Contest

from .models import FORMAT_VERSION
from .serialization import rows_to_dicts

_PASSWORD_HASH_COLUMN = "password_hash"


async def _dump(session: AsyncSession, stmt: Any) -> list[dict[str, Any]]:
    """Execute a Core select and serialize all rows to JSON-safe dicts."""
    return rows_to_dicts(list((await session.execute(stmt)).all()))


async def gather_json_members(
    session: AsyncSession,
    contest: Contest,
    *,
    include_password_hashes: bool,
    include_media: bool,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Collect the historical replay rows and render the JSON members.

    Returns:
        A ``(members, problems_payload)`` pair: the JSON archive members keyed by
        filename, and the per-problem payload the caller uses to append the bulky
        package folders.
    """
    contest_dict = rows_to_dicts(
        list((await session.execute(select(contests_t).where(contests_t.c.id == contest.id))).all())
    )[0]

    problems_payload = await _build_problems_payload(session, contest.id)
    problem_ids = [entry["problem"]["id"] for entry in problems_payload]

    user_rows = await _dump(session, select(users_t).where(users_t.c.contest_id == contest.id))
    user_ids = [u["id"] for u in user_rows]
    if not include_password_hashes:
        for user in user_rows:
            user.pop(_PASSWORD_HASH_COLUMN, None)

    site_rows = await _dump(session, select(sites_t).where(sites_t.c.contest_id == contest.id))
    language_ids = list(
        (
            await session.execute(
                select(contest_languages_t.c.language_id).where(contest_languages_t.c.contest_id == contest.id)
            )
        ).scalars()
    )

    submission_rows = (
        await _dump(session, select(submissions_t).where(submissions_t.c.problem_id.in_(problem_ids)))
        if problem_ids
        else []
    )
    submission_ids = [s["id"] for s in submission_rows]
    judgments_payload = await _build_judgments_payload(session, submission_ids)

    clarification_rows = (
        await _dump(session, select(clarifications_t).where(clarifications_t.c.team_id.in_(user_ids)))
        if user_ids
        else []
    )
    task_rows = await _dump(session, select(tasks_t).where(tasks_t.c.team_id.in_(user_ids))) if user_ids else []

    manifest = {
        "format_version": FORMAT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "includes": {
            "include_password_hashes": include_password_hashes,
            "include_media": include_media,
        },
        "contest": contest_dict,
        "sites": site_rows,
        "language_ids": language_ids,
        "problems": [
            {"original_id": problem["problem"]["id"], "ordinal": problem["problem"]["ordinal"], "dir": problem["dir"]}
            for problem in problems_payload
        ],
    }

    members: dict[str, str] = {
        "manifest.json": _json(manifest),
        "problems.json": _json(problems_payload),
        "users.json": _json(user_rows),
        "submissions.json": _json(submission_rows),
        "judgments.json": _json(judgments_payload),
        "clarifications.json": _json(clarification_rows),
        "tasks.json": _json(task_rows),
    }

    if include_media:
        media_rows = (
            await _dump(session, select(users_media_t).where(users_media_t.c.user_id.in_(user_ids))) if user_ids else []
        )
        members["media.json"] = _json(media_rows)

    return members, problems_payload


async def _build_problems_payload(session: AsyncSession, contest_id: str) -> list[dict[str, Any]]:
    """Serialize per-problem DB metadata (no bulky bytes; those go in the folder)."""
    problem_rows = await _dump(session, select(problems_t).where(problems_t.c.contest_id == contest_id))
    problem_ids = [problem["id"] for problem in problem_rows]
    if not problem_ids:
        return []
    test_cases = await _dump(session, select(test_cases_t).where(test_cases_t.c.problem_id.in_(problem_ids)))
    validators = await _dump(session, select(validators_t).where(validators_t.c.problem_id.in_(problem_ids)))
    limits = await _dump(session, select(language_limits_t).where(language_limits_t.c.problem_id.in_(problem_ids)))
    interactions = await _dump(
        session, select(sample_interactions_t).where(sample_interactions_t.c.problem_id.in_(problem_ids))
    )
    categories = (
        await session.execute(
            select(problem_categories_map_t.c.problem_id, problem_categories_t.c.name)
            .join(
                problem_categories_t,
                problem_categories_map_t.c.category_id == problem_categories_t.c.id,
            )
            .where(problem_categories_map_t.c.problem_id.in_(problem_ids))
        )
    ).all()

    tc_by_problem = _group(test_cases, "problem_id")
    limits_by_problem = _group(limits, "problem_id")
    interactions_by_problem = _group(interactions, "problem_id")
    validators_by_problem = {v["problem_id"]: v for v in validators}
    categories_by_problem: dict[str, list[str]] = {}
    for problem_id, name in categories:
        categories_by_problem.setdefault(problem_id, []).append(name)

    payload: list[dict[str, Any]] = []
    for problem in sorted(problem_rows, key=lambda item: item["ordinal"]):
        payload.append(
            {
                "problem": problem,
                "dir": f"problems/{int(problem['ordinal']):03d}",
                "test_cases": sorted(tc_by_problem.get(problem["id"], []), key=lambda item: item["ordinal"]),
                "language_limits": limits_by_problem.get(problem["id"], []),
                "sample_interactions": sorted(
                    interactions_by_problem.get(problem["id"], []), key=lambda item: item["ordinal"]
                ),
                "custom_validator": validators_by_problem.get(problem["id"]),
                "categories": categories_by_problem.get(problem["id"], []),
            }
        )
    return payload


async def _build_judgments_payload(session: AsyncSession, submission_ids: list[str]) -> list[dict[str, Any]]:
    """Serialize the FULL judgment history plus all judgment child rows."""
    if not submission_ids:
        return []
    judgments = await _dump(session, select(judgments_t).where(judgments_t.c.submission_id.in_(submission_ids)))
    judgment_ids = [j["id"] for j in judgments]
    if not judgment_ids:
        return []

    test_results = _group(
        await _dump(session, select(test_results_t).where(test_results_t.c.judgment_id.in_(judgment_ids))),
        "judgment_id",
    )
    confirmations = _group(
        await _dump(session, select(confirmations_t).where(confirmations_t.c.judgment_id.in_(judgment_ids))),
        "judgment_id",
    )
    overrides = _group(
        await _dump(session, select(verdict_overrides_t).where(verdict_overrides_t.c.judgment_id.in_(judgment_ids))),
        "judgment_id",
    )
    attempts = _group(
        await _dump(
            session, select(interactive_attempts_t).where(interactive_attempts_t.c.judgment_id.in_(judgment_ids))
        ),
        "judgment_id",
    )
    audits = _group(
        await _dump(session, select(judgment_audit_t).where(judgment_audit_t.c.judgment_id.in_(judgment_ids))),
        "judgment_id",
    )

    return [
        {
            "judgment": judgment,
            "test_results": test_results.get(judgment["id"], []),
            "confirmations": confirmations.get(judgment["id"], []),
            "overrides": overrides.get(judgment["id"], []),
            "interactive_attempts": attempts.get(judgment["id"], []),
            "audit": audits.get(judgment["id"], []),
        }
        for judgment in judgments
    ]


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """Group serialized rows by a foreign-key column value."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row[key], []).append(row)
    return grouped


def _json(value: Any) -> str:
    """Render a JSON archive member deterministically."""
    return json.dumps(value, ensure_ascii=False, indent=2)
