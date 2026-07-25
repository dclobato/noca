#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Structural guarantees of the solution-test tables."""

from __future__ import annotations

from sqlalchemy import Index

from shared.db_schema import solution_test_case_results, solution_test_runs


def test_actor_constraint_is_at_most_one_not_exactly_one() -> None:
    """Both actor FKs are SET NULL, so an `exactly one` check would block deletes.

    Deleting the triggering account nulls the column; a strict check would then be
    violated and block the delete, which is why this table can only assert
    "not both", unlike `ck_users_exactly_one_creator` whose creator FKs are not
    SET NULL.
    """
    check = next(
        constraint
        for constraint in solution_test_runs.constraints
        if getattr(constraint, "name", None) == "ck_solution_test_runs_at_most_one_actor"
    )
    expression = str(check.sqltext)  # type: ignore[attr-defined]
    assert "NOT (" in expression
    assert "IS NOT NULL AND" in expression


def test_actor_foreign_keys_are_set_null() -> None:
    """Attribution survives account deletion through the snapshotted label."""
    for column_name in ("triggered_by_user_id", "triggered_by_uberadmin_id"):
        column = solution_test_runs.c[column_name]
        assert column.nullable
        assert all(fk.ondelete == "SET NULL" for fk in column.foreign_keys)
    assert not solution_test_runs.c.triggered_by_label.nullable


def test_test_case_reference_survives_test_case_deletion() -> None:
    """Deliberate divergence from profiling_case_results, which cascades."""
    column = solution_test_case_results.c.test_case_id
    assert column.nullable
    assert all(fk.ondelete == "SET NULL" for fk in column.foreign_keys)
    # The ordinal is immutable and retained after the case is gone.
    assert not solution_test_case_results.c.ordinal.nullable


def test_case_detail_snapshots_are_optional_text_columns() -> None:
    """Ordinary runs retain bounded case files; interactive runs use transcripts."""
    for column_name in ("input_excerpt", "expected_output_excerpt", "stdout_excerpt"):
        column = solution_test_case_results.c[column_name]
        assert column.nullable
        assert column.type.python_type is str


def test_uniqueness_uses_two_partial_indexes() -> None:
    """One UniqueConstraint would silently permit duplicate ordinary rows.

    PostgreSQL treats NULLs as distinct, so a single unique constraint over
    (run, ordinal, attempt_number) would not stop two rows with the same ordinal
    and a NULL attempt_number.
    """
    indexes = {index.name: index for index in solution_test_case_results.indexes if isinstance(index, Index)}

    ordinary = indexes["uq_solution_test_case_results_ordinary"]
    assert ordinary.unique
    assert [column.name for column in ordinary.columns] == ["solution_test_run_id", "ordinal"]
    assert "attempt_number IS NULL" in str(ordinary.dialect_options["postgresql"]["where"])

    interactive = indexes["uq_solution_test_case_results_interactive"]
    assert interactive.unique
    assert [column.name for column in interactive.columns] == [
        "solution_test_run_id",
        "ordinal",
        "attempt_number",
    ]
    assert "attempt_number IS NOT NULL" in str(interactive.dialect_options["postgresql"]["where"])


def test_runs_are_scoped_by_problem_not_by_a_contest_column() -> None:
    """Listing a contest's runs joins `problems`, exactly as profiling does."""
    assert "contest_id" not in solution_test_runs.c
    composite = {index.name for index in solution_test_runs.indexes}
    assert "ix_solution_test_runs_problem_created_at" in composite


def test_buffered_solution_test_command_matches_by_identifier() -> None:
    """The purge must match a buffered SolutionTestJob by its own id, not only by
    contest_id.

    A job carrying a stale or absent contest_id would otherwise survive the purge,
    and a purge that misses buffered commands breaks the strict removal contract.
    """
    from shared.queue_schema import SolutionTestJob
    from shared.services.valkey_service.contest_purge import (
        ContestValkeyTargets,
        pending_command_belongs_to_contest,
    )

    class _Command:
        def __init__(self, job: object) -> None:
            self.job = job

    job = SolutionTestJob(
        solution_test_run_id="run-1",
        contest_id="some-other-contest",
        problem_id="problem-1",
        language_id="gcc-c17",
    )
    targets = ContestValkeyTargets(
        contest_id="target-contest",
        solution_test_run_ids=frozenset({"run-1"}),
    )

    assert "run-1" in targets.job_ids
    assert pending_command_belongs_to_contest(_Command(job), targets) is True

    unrelated = SolutionTestJob(
        solution_test_run_id="run-2",
        contest_id="some-other-contest",
        problem_id="problem-1",
        language_id="gcc-c17",
    )
    assert pending_command_belongs_to_contest(_Command(unrelated), targets) is False


def test_solution_test_job_matches_by_contest_id_too() -> None:
    """The contest_id path still works, so metrics bucketing and purge agree."""
    from shared.queue_schema import SolutionTestJob
    from shared.services.valkey_service.contest_purge import (
        ContestValkeyTargets,
        pending_command_belongs_to_contest,
    )

    class _Command:
        def __init__(self, job: object) -> None:
            self.job = job

    job = SolutionTestJob(
        solution_test_run_id="unknown-run",
        contest_id="target-contest",
        problem_id="problem-1",
        language_id="gcc-c17",
    )
    targets = ContestValkeyTargets(contest_id="target-contest")

    assert pending_command_belongs_to_contest(_Command(job), targets) is True
