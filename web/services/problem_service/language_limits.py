#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Language-limit helpers for problems."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.language_registry import default_language_registry
from web.models.language import Language
from web.models.problem import Problem, ProblemLanguageLimit

from .limit_batches import affected_limit_change_verdicts
from .models import EffectiveProblemLimits, LanguageLimitInput


async def get_language_limits_map(session: AsyncSession, problem: Problem) -> dict[str, ProblemLanguageLimit]:
    """Return a mapping of language_id to ProblemLanguageLimit for the problem."""
    result = await session.execute(select(ProblemLanguageLimit).where(ProblemLanguageLimit.problem_id == problem.id))
    return {limit.language_id: limit for limit in result.scalars().all()}


def problem_fallback_limits(problem: Problem) -> EffectiveProblemLimits:
    """Return the default problem limits used when no language override exists."""
    return EffectiveProblemLimits(
        time_limit_ms=problem.time_limit_ms,
        memory_limit_kb=problem.memory_limit_kb,
        pids_limit=problem.pids_limit,
        output_limit_in_bytes=problem.output_limit_in_bytes,
        repetitions=1,
    )


def effective_limits_for_language(
    problem: Problem,
    override: ProblemLanguageLimit | LanguageLimitInput | None,
) -> EffectiveProblemLimits:
    """Return the effective limits for one language, using fallback when needed."""
    if override is None:
        return problem_fallback_limits(problem)

    return EffectiveProblemLimits(
        time_limit_ms=int(
            override.time_limit_ms if isinstance(override, ProblemLanguageLimit) else override["time_limit_ms"]
        ),
        memory_limit_kb=int(
            override.memory_limit_kb if isinstance(override, ProblemLanguageLimit) else override["memory_limit_kb"]
        ),
        pids_limit=int(override.pids_limit if isinstance(override, ProblemLanguageLimit) else override["pids_limit"]),
        output_limit_in_bytes=(
            override.output_limit_in_bytes
            if isinstance(override, ProblemLanguageLimit)
            else int(override["output_limit_in_bytes"])
            if override.get("output_limit_in_bytes")
            else None
        ),
        repetitions=(
            override.repetitions
            if isinstance(override, ProblemLanguageLimit)
            else int(override["repetitions"])
            if override.get("repetitions") is not None
            else 1
        ),
    )


def submitted_language_limits(
    languages: list[Language],
    submitted_form: Mapping[str, object],
    existing_limits: dict[str, ProblemLanguageLimit],
) -> dict[str, LanguageLimitInput]:
    """Extract the posted per-language limits, preserving repetitions from existing rows."""
    default_registry = default_language_registry()
    limits: dict[str, LanguageLimitInput] = {}
    for language in languages:
        time_value = str(submitted_form.get(f"lang_time_{language.id}", "")).strip()
        memory_value = str(submitted_form.get(f"lang_mem_{language.id}", "")).strip()
        pids_value = str(submitted_form.get(f"lang_pids_{language.id}", "")).strip()
        output_value = str(submitted_form.get(f"lang_out_{language.id}", "")).strip()
        if not time_value and not memory_value and not pids_value:
            continue

        existing_limit = existing_limits.get(language.id)
        default_language = default_registry.get(language.id)
        limits[language.id] = {
            "time_limit_ms": time_value,
            "memory_limit_kb": memory_value,
            "pids_limit": pids_value,
            "output_limit_in_bytes": output_value,
            "repetitions": (
                existing_limit.repetitions
                if existing_limit is not None
                else default_language.profiling_repetitions_default
                if default_language is not None
                else 1
            ),
        }
    return limits


async def upsert_language_limits(
    session: AsyncSession,
    problem: Problem,
    limits: dict[str, LanguageLimitInput],
) -> None:
    """Delete and re-insert all per-language limits for a problem."""
    existing_limits = await get_language_limits_map(session, problem)
    default_registry = default_language_registry()
    await session.execute(delete(ProblemLanguageLimit).where(ProblemLanguageLimit.problem_id == problem.id))
    for language_id, fields in limits.items():
        if not fields.get("time_limit_ms") and not fields.get("memory_limit_kb") and not fields.get("pids_limit"):
            continue
        existing_limit = existing_limits.get(language_id)
        repetitions_value = fields.get("repetitions")
        if repetitions_value is None:
            if existing_limit is not None:
                repetitions = existing_limit.repetitions
            else:
                default_language = default_registry.get(language_id)
                repetitions = default_language.profiling_repetitions_default if default_language is not None else 1
        else:
            repetitions = int(repetitions_value)
        limit = ProblemLanguageLimit(
            problem_id=problem.id,
            language_id=language_id,
            time_limit_ms=int(fields["time_limit_ms"]),
            memory_limit_kb=int(fields["memory_limit_kb"]),
            pids_limit=int(fields["pids_limit"]),
            output_limit_in_bytes=int(fields["output_limit_in_bytes"]) if fields.get("output_limit_in_bytes") else None,
            repetitions=repetitions,
        )
        session.add(limit)
    await session.flush()


async def apply_fallback_limits(session: AsyncSession, problem: Problem) -> bool:
    """Copy per-column maxima from language limits into the problem fallback limits."""
    result = await session.execute(
        select(
            func.max(ProblemLanguageLimit.time_limit_ms),
            func.max(ProblemLanguageLimit.memory_limit_kb),
            func.max(ProblemLanguageLimit.pids_limit),
            func.max(ProblemLanguageLimit.output_limit_in_bytes),
        ).where(ProblemLanguageLimit.problem_id == problem.id)
    )
    row = result.one()
    if row[0] is None or row[1] is None or row[2] is None:
        return False

    problem.time_limit_ms = int(row[0])
    problem.memory_limit_kb = int(row[1])
    problem.pids_limit = int(row[2])
    problem.output_limit_in_bytes = int(row[3]) if row[3] is not None else None
    await session.flush()
    return True


def changed_effective_limits(
    problem: Problem,
    languages: list[Language],
    *,
    before_overrides: Mapping[str, ProblemLanguageLimit],
    after_overrides: Mapping[str, ProblemLanguageLimit | LanguageLimitInput],
    before_fallback: EffectiveProblemLimits | None = None,
    after_fallback: EffectiveProblemLimits | None = None,
) -> dict[str, tuple[str, EffectiveProblemLimits, EffectiveProblemLimits]]:
    """Return language IDs whose effective limits changed."""
    before_default = before_fallback or problem_fallback_limits(problem)
    after_default = after_fallback or problem_fallback_limits(problem)
    changed: dict[str, tuple[str, EffectiveProblemLimits, EffectiveProblemLimits]] = {}

    for language in languages:
        before_override = before_overrides.get(language.id)
        after_override = after_overrides.get(language.id)
        before_limits = (
            before_default if before_override is None else effective_limits_for_language(problem, before_override)
        )
        after_limits = (
            after_default if after_override is None else effective_limits_for_language(problem, after_override)
        )
        if before_limits == after_limits:
            continue

        change_kind = "fallback" if before_override is None and after_override is None else "explicit"
        changed[language.id] = (change_kind, before_limits, after_limits)

    return changed


__all__ = [
    "affected_limit_change_verdicts",
    "apply_fallback_limits",
    "changed_effective_limits",
    "effective_limits_for_language",
    "get_language_limits_map",
    "problem_fallback_limits",
    "submitted_language_limits",
    "upsert_language_limits",
]
