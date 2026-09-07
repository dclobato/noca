#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Restore submissions, judgment history, clarifications, and tasks."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Table, insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import (
    clarifications as clarifications_t,
)
from shared.db_schema import (
    human_submission_confirmations as confirmations_t,
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
    verdict_overrides as verdict_overrides_t,
)

from .models import RestoreState, remap_optional
from .serialization import build_insert_values


async def restore_submissions(
    session: AsyncSession,
    submissions: list[dict[str, Any]],
    state: RestoreState,
) -> None:
    """Restore submissions with problem and team references remapped."""
    for submission in submissions:
        new_id = str(uuid.uuid4())
        state.submission_map[submission["id"]] = new_id
        await session.execute(
            insert(submissions_t),
            [
                build_insert_values(
                    submissions_t,
                    submission,
                    overrides={
                        "id": new_id,
                        "problem_id": state.problem_map[submission["problem_id"]],
                        "team_id": state.user_map[submission["team_id"]],
                    },
                )
            ],
        )


async def restore_judgments(
    session: AsyncSession,
    judgments: list[dict[str, Any]],
    state: RestoreState,
) -> None:
    """Restore judgments and every persisted child row."""
    for entry in judgments:
        judgment = entry["judgment"]
        new_id = str(uuid.uuid4())
        state.judgment_map[judgment["id"]] = new_id
        submission_id = state.submission_map[judgment["submission_id"]]
        await session.execute(
            insert(judgments_t),
            [build_insert_values(judgments_t, judgment, overrides={"id": new_id, "submission_id": submission_id})],
        )
        await _restore_judgment_children(session, entry, new_id, submission_id, state)


async def _restore_judgment_children(
    session: AsyncSession,
    entry: dict[str, Any],
    judgment_id: str,
    submission_id: str,
    state: RestoreState,
) -> None:
    child_groups: tuple[tuple[str, Table, dict[str, dict[str, str]]], ...] = (
        ("test_results", test_results_t, {"test_case_id": state.test_case_map}),
        ("confirmations", confirmations_t, {"judge_id": state.user_map}),
        ("overrides", verdict_overrides_t, {"overridden_by": state.user_map}),
        ("interactive_attempts", interactive_attempts_t, {}),
        ("audit", judgment_audit_t, {"actor_user_id": state.user_map}),
    )
    for key, table, remappings in child_groups:
        for row in entry[key]:
            overrides: dict[str, Any] = {"id": str(uuid.uuid4()), "judgment_id": judgment_id}
            if "submission_id" in table.c:
                overrides["submission_id"] = submission_id
            for column, mapping in remappings.items():
                overrides[column] = remap_optional(mapping, row.get(column))
            await session.execute(insert(table), [build_insert_values(table, row, overrides=overrides)])


async def restore_clarifications(
    session: AsyncSession,
    clarifications: list[dict[str, Any]],
    state: RestoreState,
) -> None:
    """Restore clarifications and all actor references.

    ``is_announcement`` comes straight from the archive: the only supported version
    states it, and validation refused any archive that omits it, so there is nothing
    left to infer from the author's recorded role.

    ``acquired_at`` / ``acquired_timestamp_seconds`` are contest history for an
    *answered* clarification -- restored as archived, the same as ``answered_at``.
    For one still open, they describe a Valkey lock that is not restored along
    with it, so they are cleared: left archived, they would report a growing
    service time against a handler who no longer holds anything.
    """
    for clarification in clarifications:
        answered = clarification.get("answered_at") is not None
        await session.execute(
            insert(clarifications_t),
            [
                build_insert_values(
                    clarifications_t,
                    clarification,
                    overrides={
                        "id": str(uuid.uuid4()),
                        "team_id": state.user_map[clarification["team_id"]],
                        "problem_id": remap_optional(state.problem_map, clarification.get("problem_id")),
                        "judge_id": remap_optional(state.user_map, clarification.get("judge_id")),
                        "hidden_by_judge_id": remap_optional(state.user_map, clarification.get("hidden_by_judge_id")),
                        "hidden_by_admin_id": remap_optional(state.user_map, clarification.get("hidden_by_admin_id")),
                        **({} if answered else {"acquired_at": None, "acquired_timestamp_seconds": None}),
                    },
                )
            ],
        )


async def restore_tasks(session: AsyncSession, tasks: list[dict[str, Any]], state: RestoreState) -> None:
    """Restore staff tasks with nullable actors and problems remapped.

    ``acquired_at`` / ``acquired_timestamp_seconds`` are contest history for a
    *finished* task -- restored as archived, the same as ``finished_at``. For
    one still open, they describe a Valkey lock that is not restored along with
    it, so they are cleared: left archived, they would report a growing service
    time against a handler who no longer holds anything.
    """
    for task in tasks:
        finished = task.get("finished_at") is not None
        await session.execute(
            insert(tasks_t),
            [
                build_insert_values(
                    tasks_t,
                    task,
                    overrides={
                        "id": str(uuid.uuid4()),
                        "team_id": state.user_map[task["team_id"]],
                        "staff_id": remap_optional(state.user_map, task.get("staff_id")),
                        "problem_id": remap_optional(state.problem_map, task.get("problem_id")),
                        **({} if finished else {"acquired_at": None, "acquired_timestamp_seconds": None}),
                    },
                )
            ],
        )
