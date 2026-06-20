#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Profiling-run helpers for problems."""

from __future__ import annotations

import hashlib
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import ProfilingStatus
from shared.language_registry import default_language_registry
from shared.queue_schema import ProfilingJob
from web.models.problem import Problem, ProfilingRun
from web.services.valkey_service import ValkeyRuntime
from web.services.valkey_service import enqueue_profiling_job as enqueue_profiling_job_to_valkey


async def create_profiling_run(
    session: AsyncSession,
    problem: Problem,
    language_id: str,
    source_code: str,
    safety_factor: float,
    triggered_by_user_id: str | None,
) -> ProfilingRun:
    """Create and flush a queued profiling run for a problem/language pair."""
    profiling_run = ProfilingRun(
        problem_id=problem.id,
        language_id=language_id,
        source_code=source_code,
        source_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
        status=ProfilingStatus.QUEUED,
        safety_factor=safety_factor,
        triggered_by_user_id=triggered_by_user_id,
    )
    session.add(profiling_run)
    await session.flush()
    return profiling_run


async def get_profiling_runs_for_problem(session: AsyncSession, problem: Problem) -> list[ProfilingRun]:
    """Return profiling runs for a problem ordered newest first."""
    result = await session.execute(
        select(ProfilingRun)
        .where(ProfilingRun.problem_id == problem.id)
        .options(selectinload(ProfilingRun.case_results), selectinload(ProfilingRun.language))
        .order_by(ProfilingRun.created_at.desc(), ProfilingRun.id.desc())
    )
    return list(result.scalars().all())


async def get_active_profiling_run_for_problem(session: AsyncSession, problem: Problem) -> ProfilingRun | None:
    """Return the newest profiling run still in progress for the problem, if any."""
    result = await session.execute(
        select(ProfilingRun)
        .where(
            ProfilingRun.problem_id == problem.id,
            ProfilingRun.status.in_(
                (
                    ProfilingStatus.QUEUED,
                    ProfilingStatus.DISPATCHED,
                    ProfilingStatus.RUNNING,
                )
            ),
        )
        .options(selectinload(ProfilingRun.case_results), selectinload(ProfilingRun.language))
        .order_by(ProfilingRun.created_at.desc(), ProfilingRun.id.desc())
    )
    return result.scalar_one_or_none()


def compute_profiling_limits_map(profiling_runs: list[ProfilingRun]) -> dict[str, dict[str, int | None]]:
    """Return expected per-language limits from the latest DONE profiling run per language."""
    default_registry = default_language_registry()
    result: dict[str, dict[str, int | None]] = {}
    seen: set[str] = set()
    for run in profiling_runs:
        if run.status != ProfilingStatus.DONE:
            continue
        if run.language_id in seen:
            continue
        cases = run.case_results
        if not cases:
            continue
        seen.add(run.language_id)
        max_time = max((row.total_wall_time_ms for row in cases if row.total_wall_time_ms is not None), default=None)
        max_mem = max((row.peak_memory_kb for row in cases if row.peak_memory_kb is not None), default=None)
        max_pids = max((row.peak_pids for row in cases if row.peak_pids is not None), default=None)
        max_out = max((row.peak_output_bytes for row in cases if row.peak_output_bytes is not None), default=None)
        default_language = default_registry.get(run.language_id)
        pids_floor = default_language.profiled_pids_floor if default_language is not None else 32
        result[run.language_id] = {
            "time_limit_ms": max(1, math.ceil(run.safety_factor * max_time)) if max_time is not None else None,
            "memory_limit_kb": max(1, math.ceil(run.safety_factor * max_mem)) if max_mem is not None else None,
            "pids_limit": (
                max(pids_floor, math.ceil(run.safety_factor * max_pids)) if max_pids is not None else pids_floor
            ),
            "output_limit_in_bytes": max(1, math.ceil(run.safety_factor * max_out)) if max_out is not None else None,
        }
    return result


async def enqueue_profiling_job(
    valkey_runtime: ValkeyRuntime,
    profiling_run: ProfilingRun,
    *,
    contest_id: str,
) -> None:
    """Enqueue a profiling run in the dedicated profiling queue."""
    await enqueue_profiling_job_to_valkey(
        valkey_runtime,
        ProfilingJob(
            profiling_run_id=profiling_run.id,
            contest_id=contest_id,
            problem_id=profiling_run.problem_id,
            language_id=profiling_run.language_id,
        ),
    )
