#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import TypedDict

from fastapi import Request

from web.dependencies import ContestAdminContext
from web.models.language import Language
from web.models.problem import Problem, ProblemLimitChangeBatch, ProblemLimitChangeBatchSubmission
from web.routes.contest_admin_problem_helpers import _is_edit_allowed, _is_limits_edit_allowed
from web.services.problem_service import (
    compute_profiling_limits_map,
    get_active_profiling_run_for_problem,
    get_contest_languages,
    get_language_limits_map,
    get_profiling_runs_for_problem,
)


class LimitBatchGroup(TypedDict):
    """Language-scoped view model for one limit-change batch section."""

    language: Language
    change_kind: str
    before_limits: object
    after_limits: object
    rows: list[ProblemLimitChangeBatchSubmission]
    pending_count: int
    queued_count: int
    stale_count: int


async def _build_profiling_limits_context(
    request: Request,
    ctx: ContestAdminContext,
    problem: Problem,
    form_data: Mapping[str, object],
) -> dict[str, object]:
    """Build shared Auto-Limit/per-language-limits context for edit routes."""
    languages = await get_contest_languages(ctx.session, ctx.contest)
    limits_map = await get_language_limits_map(ctx.session, problem)
    profiling_runs = await get_profiling_runs_for_problem(ctx.session, problem)
    active_profiling_run = await get_active_profiling_run_for_problem(ctx.session, problem)
    latest_profiling_run = profiling_runs[0] if profiling_runs else None
    selected_language_id = (
        active_profiling_run.language_id
        if active_profiling_run is not None
        else latest_profiling_run.language_id
        if latest_profiling_run is not None
        else languages[0].id
        if languages
        else None
    )
    return {
        "request": request,
        "contest": ctx.contest,
        "problem": problem,
        "languages": languages,
        "limits_map": limits_map,
        "form_data": form_data,
        "is_edit_allowed": _is_edit_allowed(ctx.contest),
        "is_limits_edit_allowed": _is_limits_edit_allowed(ctx.contest),
        "active_profiling_run": active_profiling_run,
        "latest_profiling_run": latest_profiling_run,
        "profiling_selected_language_id": selected_language_id,
        "profiling_computed_limits_map": compute_profiling_limits_map(profiling_runs),
    }


def _limit_batch_groups(batch: ProblemLimitChangeBatch) -> list[LimitBatchGroup]:
    """Build language-grouped submission rows for the limit-change review page."""
    grouped: dict[str, LimitBatchGroup] = {}
    submissions_by_language: defaultdict[str, list[ProblemLimitChangeBatchSubmission]] = defaultdict(list)
    for batch_submission in batch.submissions:
        submissions_by_language[batch_submission.language_id].append(batch_submission)

    for batch_language in batch.languages:
        rows = submissions_by_language.get(batch_language.language_id, [])
        grouped[batch_language.language_id] = {
            "language": batch_language.language,
            "change_kind": batch_language.change_kind,
            "before_limits": batch_language.before_limits,
            "after_limits": batch_language.after_limits,
            "rows": rows,
            "pending_count": sum(1 for row in rows if row.rejudge_status == "PENDING"),
            "queued_count": sum(1 for row in rows if row.rejudge_status == "QUEUED"),
            "stale_count": sum(1 for row in rows if row.rejudge_status == "STALE"),
        }

    grouped_entries = list(grouped.values())
    grouped_entries.sort(key=lambda entry: entry["language"].name)
    return grouped_entries


def _validate_language_limit_inputs(
    languages: list[Language],
    form: Mapping[str, object],
) -> list[str]:
    """Validate optional per-language limit rows from the shared edit form."""
    errors: list[str] = []
    for language in languages:
        time_value = str(form.get(f"lang_time_{language.id}", "")).strip()
        memory_value = str(form.get(f"lang_mem_{language.id}", "")).strip()
        pids_value = str(form.get(f"lang_pids_{language.id}", "")).strip()
        output_value = str(form.get(f"lang_out_{language.id}", "")).strip()

        if not time_value and not memory_value and not pids_value and not output_value:
            continue
        if not time_value or not memory_value or not pids_value:
            errors.append(f"{language.name}: time, memory, and PIDs must be filled together.")
            continue

        for label, raw_value in (
            ("time", time_value),
            ("memory", memory_value),
            ("PIDs", pids_value),
        ):
            try:
                if int(raw_value) < 1:
                    raise ValueError
            except ValueError:
                errors.append(f"{language.name}: {label} limit must be a positive integer.")

        if output_value:
            try:
                if int(output_value) < 1:
                    raise ValueError
            except ValueError:
                errors.append(f"{language.name}: output limit must be a positive integer or blank.")

    return errors
